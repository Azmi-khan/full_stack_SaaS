from datetime import datetime, timedelta, timezone
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi import File, UploadFile
from fastapi.responses import FileResponse
import shutil
import os
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from passlib.context import CryptContext
import jwt
import models, schemas
from database import engine, get_db
from worker import process_video_task
from fastapi import WebSocket
import asyncio
from celery.result import AsyncResult

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

#cryptography settings
SECRET_KEY = "my_brand_new_secret_key_123"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30



#password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    

#user signup

@app.post("/signup", response_model=schemas.UserResponse)

def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    #checking if the user is existing or not
    db_user = db.query(models.User).filter(models.User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    #hashing the password
    hashed_password = get_password_hash(user.password)

    #creating a new user instance
    new_user = models.User(email=user.email, hashed_password=hashed_password)

    #saving the new user to the database
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user



#user login and token generation
@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}  


#token vlidation and access to secure data
@app.get("/secure-data")

def get_secure_data(current_user_id: str = Depends(get_current_user)):
    return {
        "message": "Access Granted! You have a valid token.",
        "user_id": current_user_id
    }

#uploading pdf files
UPLOAD_DIR = "uploaded_pdf"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/upload")
def upload_pdf(
    file: UploadFile = File(...),
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)

):
    if not (file.filename.endswith(".mp4") or file.filename.endswith(".avi")):
        raise HTTPException(status_code=400, detail="Only MP4 or AVI video files are allowed.")
    
    file_location = f"{UPLOAD_DIR}/{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    new_doc = models.PDFDocument(
        filename=file.filename,
        user_id=int(current_user_id)
    )
    db.add(new_doc)
    db.commit()
    db.refresh(new_doc)
    task = process_video_task.delay(new_doc.filename)

    return {
        "message": "File uploaded successfully and processing started in the background.",
        "document_id": new_doc.id,
        "filename": new_doc.filename,
        "task_id": task.id
    }

#checking the documents of the user
@app.get("/documents")
def get_user_documents(
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user_docs = db.query(models.PDFDocument).filter(
        models.PDFDocument.user_id == int(current_user_id)
    ).all()

    return {"documents" : user_docs}

#deleting documents

@app.delete("/documents/{document_id}")
def delete_document(
    document_id: int,
    current_user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    doc = db.query(models.PDFDocument).filter(
        models.PDFDocument.id == document_id,
        models.PDFDocument.user_id == int(current_user_id)
    ).first()

    if not doc:
        raise HTTPException(status_code=404,                
            detail="Document not found or you don't have permission to delete it."
    )
    
    #delete the file from the server
    file_path = f"{UPLOAD_DIR}/{doc.filename}"
    if os.path.exists(file_path):
        os.remove(file_path)

    #delete the record from the database
    db.delete(doc)
    db.commit()

    return {"message": f"'{doc.filename}' deleted successfully."}

@app.get("/documents")
def get_documents(current_user_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    
    docs = db.query(models.PDFDocument).filter(models.PDFDocument.user_id == int(current_user_id)).all()
    return docs

@app.websocket("/ws/task/{task_id}")
async def websocket_task_status(websocket: WebSocket, task_id: str):
    await websocket.accept() 
    try:
        while True:
            
            result = AsyncResult(task_id)
            
            if result.state == 'PROGRESS':
                
                await websocket.send_json({"progress": result.info.get('progress', 0)})
            elif result.state == 'SUCCESS':
                
                await websocket.send_json({"progress": 100, "status": "completed"})
                break
                
            await asyncio.sleep(1) 
    except Exception as e:
        print("WebSocket disconnected")

@app.get("/video/{filename}")
def get_processed_video(filename: str):
    file_path = f"{UPLOAD_DIR}/{filename}"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/webm")
    raise HTTPException(status_code=404, detail="Processed video not found")