import os
import time
import jwt
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

class ZoomSignatureService:
    def __init__(self):
        self.sdk_key = os.getenv("ZOOM_CLIENT_ID")
        self.sdk_secret = os.getenv("ZOOM_CLIENT_SECRET")
        
        if not self.sdk_key or not self.sdk_secret:
            raise ValueError("Missing ZOOM_SDK_KEY or ZOOM_SDK_SECRET")
    
    def generate_signature(self, meeting_number: str, role: int) -> dict:
        try:
            iat = int(time.time())
            exp = iat + 60 * 2
            token_exp = iat + 60 * 60
            
            payload = {
                "sdkKey": self.sdk_key,
                "mn": meeting_number,
                "role": role,
                "iat": iat,
                "exp": exp,
                "tokenExp": token_exp,
            }
            
            signature = jwt.encode(payload, self.sdk_secret, algorithm="HS256")
            return {"signature": signature, "sdkKey": self.sdk_key}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))