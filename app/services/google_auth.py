from google.oauth2 import id_token
from google.auth.transport import requests

from app.core.config import settings



def verify_google_id_token(token:str)->dict:

    try:
        google_user = id_token.verify_oauth2_token(
                token,
                requests.Request(),
                settings.google_web_client_id,
        )

        return google_user

    except ValueError as error:
        raise ValueError(
                f"Invalid Google Id token: {error}"
            )



