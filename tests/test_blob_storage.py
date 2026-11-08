from types import SimpleNamespace

from app.api import users
from app.services import blob_storage


def test_delete_prefix_removes_objects_from_each_list_page(monkeypatch):
    deleted = []

    class Paginator:
        def paginate(self, **kwargs):
            assert kwargs == {"Bucket": "uploads", "Prefix": "users/user-1/"}
            return [
                {"Contents": [{"Key": "users/user-1/avatar.png"}]},
                {"Contents": [{"Key": "users/user-1/challenges/image.png"}]},
                {},
            ]

    class Client:
        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return Paginator()

        def delete_objects(self, **kwargs):
            deleted.extend(item["Key"] for item in kwargs["Delete"]["Objects"])
            return {}

    monkeypatch.setattr(blob_storage, "_client", lambda bucket: Client())

    blob_storage.delete_prefix("users/user-1/", "uploads")

    assert deleted == [
        "users/user-1/avatar.png",
        "users/user-1/challenges/image.png",
    ]


def test_account_deletion_cleans_user_and_challenge_storage(monkeypatch):
    user = SimpleNamespace(id="user-1", is_admin=False)
    owned_challenge = SimpleNamespace(id="owned")
    responses = iter(
        [
            [owned_challenge],
            [],
            ["participated"],
            ["evidence/user-file.mp4", "evidence/challenge-file.mp4"],
        ]
    )
    deleted_prefixes = []
    deleted_blobs = []

    class ScalarResult:
        def all(self):
            return next(responses)

    class Db:
        def scalars(self, statement):
            return ScalarResult()

        def delete(self, value):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(users.settings, "r2_endpoint_url", "https://r2.example")
    monkeypatch.setattr(users.settings, "r2_access_key_id", "key")
    monkeypatch.setattr(users.settings, "r2_secret_access_key", "secret")
    monkeypatch.setattr(users.settings, "r2_bucket", "uploads")
    monkeypatch.setattr(users.settings, "r2_evidence_bucket", "evidence")
    monkeypatch.setattr(
        users, "delete_prefix", lambda prefix, bucket: deleted_prefixes.append((prefix, bucket))
    )
    monkeypatch.setattr(
        users, "delete_blob", lambda key, bucket: deleted_blobs.append((key, bucket))
    )

    users.delete_my_account(Db(), user)

    assert ("users/user-1/", "uploads") in deleted_prefixes
    assert ("challenges/participated/users/user-1/", "evidence") in deleted_prefixes
    assert ("challenges/owned/", "evidence") in deleted_prefixes
    assert set(deleted_blobs) == {
        ("evidence/user-file.mp4", "evidence"),
        ("evidence/challenge-file.mp4", "evidence"),
    }