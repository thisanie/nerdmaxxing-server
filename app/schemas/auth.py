from pydantic import BaseModel

class GoogleAuthRequest(BaseModel):
    id_token : str
