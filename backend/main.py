from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="WappFlow API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
from routers.settings import router as settings_router
from routers.file_forward import router as file_forward_router
from routers.bulk_message import router as bulk_message_router
from routers.chatbot import router as chatbot_router
from routers.logs import router as logs_router
from routers.webhook import router as webhook_router

app.include_router(settings_router)
app.include_router(file_forward_router)
app.include_router(bulk_message_router)
app.include_router(chatbot_router)
app.include_router(logs_router)
app.include_router(webhook_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "WappFlow API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
