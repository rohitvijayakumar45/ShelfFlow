import time
import jwt
import hashlib
import secrets
import hmac
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
from backend.config import JWT_SECRET

bearer = HTTPBearer()

def hash_password(password):
    salt=secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,maxmem=64*1024*1024)
    return salt.hex()+":"+digest.hex()

def verify_password(password,encoded):
    try:
        salt,digest=encoded.split(':')
        actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,maxmem=64*1024*1024)
        return hmac.compare_digest(actual.hex(),digest)
    except ValueError:return False

def issue(role, subject):
    return jwt.encode({"role": role, "sub": subject, "iat": int(time.time()), "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm="HS256")

def decode(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub", "role"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired authentication")

def authenticated(credentials=Depends(bearer)):
    return decode(credentials.credentials)

def staff(user=Depends(authenticated)):
    if user["role"] != "staff":
        raise HTTPException(403, "Staff authorization required")
    return user

def customer(user=Depends(authenticated)):
    if user["role"] != "customer":
        raise HTTPException(403, "Customer authorization required")
    return user
