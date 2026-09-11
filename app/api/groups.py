from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_db
from app.models.group import Group, GroupMembership
from app.models.user import User
from app.schemas.group import (
    GroupCreate,
    GroupJoinRequestResponse,
    GroupMembershipResponse,
    GroupResponse,
)


router = APIRouter(prefix="/api/v1/groups", tags=["Groups"])


def group_response(db: Session, group: Group, viewer_id: str | None = None) -> GroupResponse:
    membership_status = None
    if viewer_id is not None:
        membership_status = db.scalar(
            select(GroupMembership.status).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == viewer_id,
            )
        )
    member_count = db.scalar(
        select(func.count())
        .select_from(GroupMembership)
        .where(GroupMembership.group_id == group.id, GroupMembership.status == "ACTIVE")
    ) or 0
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        visibility=group.visibility,
        creator_id=group.creator_id,
        member_count=member_count,
        membership_status=membership_status,
        created_at=group.created_at,
    )


def get_group_or_404(db: Session, group_id: str) -> Group:
    group = db.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    return group


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupResponse:
    group = Group(
        name=payload.name.strip(),
        description=payload.description,
        visibility=payload.visibility,
        creator_id=current_user.id,
    )
    if not group.name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Group name cannot be blank.")
    db.add(group)
    db.flush()
    db.add(GroupMembership(group_id=group.id, user_id=current_user.id, status="ACTIVE"))
    db.commit()
    db.refresh(group)
    return group_response(db, group, current_user.id)


@router.get("", response_model=list[GroupResponse])
def list_public_groups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[GroupResponse]:
    groups = db.scalars(
        select(Group)
        .where(Group.visibility == "PUBLIC")
        .order_by(Group.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [group_response(db, group, current_user.id) for group in groups]


@router.get("/me", response_model=list[GroupResponse])
def list_my_groups(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[GroupResponse]:
    groups = db.scalars(
        select(Group)
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .where(
            GroupMembership.user_id == current_user.id,
            GroupMembership.status == "ACTIVE",
        )
        .order_by(Group.created_at.desc())
    ).all()
    return [group_response(db, group, current_user.id) for group in groups]


@router.get("/{group_id}", response_model=GroupResponse)
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupResponse:
    return group_response(db, get_group_or_404(db, group_id), current_user.id)


@router.post("/{group_id}/join", response_model=GroupMembershipResponse, status_code=status.HTTP_201_CREATED)
def join_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupMembershipResponse:
    group = get_group_or_404(db, group_id)
    existing = db.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id,
            GroupMembership.user_id == current_user.id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You already belong to or requested to join this group.")
    membership = GroupMembership(
        group_id=group.id,
        user_id=current_user.id,
        status="ACTIVE" if group.visibility == "PUBLIC" else "PENDING",
    )
    db.add(membership)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You already belong to or requested to join this group.")
    db.refresh(membership)
    return membership


@router.delete("/{group_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    group = get_group_or_404(db, group_id)
    if group.creator_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The group creator cannot leave the group.")
    membership = db.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id,
            GroupMembership.user_id == current_user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="You are not a member of this group.")
    db.delete(membership)
    db.commit()


@router.get("/{group_id}/join-requests", response_model=list[GroupJoinRequestResponse])
def list_join_requests(
    group_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[GroupJoinRequestResponse]:
    group = get_group_or_404(db, group_id)
    if group.creator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the group creator can manage join requests.")
    rows = db.execute(
        select(GroupMembership, User)
        .join(User, User.id == GroupMembership.user_id)
        .where(GroupMembership.group_id == group.id, GroupMembership.status == "PENDING")
        .order_by(GroupMembership.created_at.asc())
    ).all()
    return [
        GroupJoinRequestResponse(
            id=membership.id,
            group_id=membership.group_id,
            user_id=membership.user_id,
            status=membership.status,
            created_at=membership.created_at,
            username=user.username,
            display_name=user.display_name,
        )
        for membership, user in rows
    ]


def resolve_join_request(
    group_id: str,
    user_id: str,
    db: Session,
    current_user: User,
    new_status: str,
) -> GroupMembershipResponse | None:
    group = get_group_or_404(db, group_id)
    if group.creator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the group creator can manage join requests.")
    membership = db.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id,
            GroupMembership.user_id == user_id,
            GroupMembership.status == "PENDING",
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Join request not found.")
    if new_status == "ACTIVE":
        membership.status = new_status
        db.commit()
        db.refresh(membership)
        return membership
    db.delete(membership)
    db.commit()
    return None


@router.post("/{group_id}/join-requests/{user_id}/approve", response_model=GroupMembershipResponse)
def approve_join_request(
    group_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GroupMembershipResponse:
    result = resolve_join_request(group_id, user_id, db, current_user, "ACTIVE")
    assert result is not None
    return result


@router.delete("/{group_id}/join-requests/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def reject_join_request(
    group_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    resolve_join_request(group_id, user_id, db, current_user, "REJECTED")