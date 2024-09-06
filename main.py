import io
import logging
import json
# from cv2 import log
import requests
import os
import base64
from typing import List, Union, Dict, Any, Generator
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
import subprocess
from starlette.responses import StreamingResponse, JSONResponse
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer
from config import DB_FILE
from enum import Enum
from contextlib import asynccontextmanager
from log_config import setup_logging, logging_config
from webrtcstreamer import WebRTCStreamer

setup_logging()
load_dotenv()
CAMERA_API_VERSION = os.environ.get("CAMERA_API_VERSION") or "1.0"
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME") or "admin"
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD") or "admin123"

logger = logging.getLogger("app")

@asynccontextmanager
async def lifespan(app: FastAPI):
    DatabaseManager.check_connections()
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CameraStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class DeviceInfo(BaseModel):
    ip: str
    serialNumber: str
    deviceName: str
    model: str
    firmwareVersion: str
    status: CameraStatus = CameraStatus.ACTIVE

class CaptureResponse(BaseModel):
    ip: str
    data: str

class GenericResponse(BaseModel):
    success: bool
    data: Union[DeviceInfo, List[DeviceInfo], List[CaptureResponse], str] = None

class Camera:
    def __init__(self, ip: str):
        self.ip = ip
        self.auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)

    def test_connection(self) -> DeviceInfo:
        url = f"http://{self.ip}/ISAPI/System/deviceInfo"
        try:
            response = requests.get(url, auth=self.auth, timeout=1)
            response.raise_for_status()
            device_info_xml = response.text
            return self.parse_device_info_xml(device_info_xml)
        except requests.RequestException as e:
            raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e)}")

    def parse_device_info_xml(self, xml_string: str) -> DeviceInfo:
        ns = {"ns": "http://www.hikvision.com/ver20/XMLSchema"}
        root = ET.fromstring(xml_string)
        return DeviceInfo(
            ip=self.ip,
            serialNumber=root.find("ns:serialNumber", ns).text,
            deviceName=root.find("ns:deviceName", ns).text,
            model=root.find("ns:model", ns).text,
            firmwareVersion=root.find("ns:firmwareVersion", ns).text
        )

    def capture_image(self) -> bytes:
        url = f"http://{self.ip}/ISAPI/Streaming/channels/1/picture"
        try:
            response = requests.get(url, auth=self.auth, timeout=10)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            raise HTTPException(status_code=500, detail=f"Failed to capture image: {str(e)}")
        
    async def webrtc_stream(self, websocket: WebSocket) -> None:
        await websocket.accept()
        
        streamer = WebRTCStreamer(self.ip, CAMERA_USERNAME, CAMERA_PASSWORD)
        streamer.ws = websocket

        try:
            offer = await streamer.create_offer()
            await websocket.send_json({"type": "offer", "data": offer})

            while not streamer.closed:
                try:
                    message = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
                    if message["type"] == "answer":
                        await streamer.handle_answer(message["data"])
                    elif message["type"] == "candidate":
                        await streamer.add_ice_candidate(message["data"])
                    elif message["type"] == "close":
                        break
                except asyncio.TimeoutError:
                    if streamer.pc and streamer.pc.iceConnectionState == "failed":
                        logger.warning("ICE connection failed. Closing connection.")
                        break
                    continue
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {str(e)}")
                    break

        except Exception as e:
            logger.error(f"WebRTC streaming error for {self.ip}: {str(e)}")
        finally:
            await streamer.close()

class DatabaseManager:
    @staticmethod
    def load_db() -> List[Dict[str, Any]]:
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    @staticmethod
    def save_db(data: List[Dict[str, Any]]) -> None:
        with open(DB_FILE, "w") as f:
            json.dump(data, f)

    @staticmethod
    def check_connections() -> None:
        db = DatabaseManager.load_db()
        connections = []
        for camera_data in db:
            camera = Camera(camera_data["ip"])
            try:
                camera.test_connection()
                camera_data["status"] = CameraStatus.ACTIVE
            except HTTPException:
                camera_data["status"] = CameraStatus.INACTIVE
            connections.append(camera_data)
        DatabaseManager.save_db(connections)


@app.websocket("/stream/{camera_ip}")
async def webrtc_endpoint(websocket: WebSocket, camera_ip: str):
    """
    check_camera_ip_exists_and_active(camera_ip)

    try:
        check_camera_working(camera_ip)
    except HTTPException as e:
        await websocket.close(code=4000, reason=str(e.detail))
    else:
        camera = Camera(camera_ip)
        
        await camera.webrtc_stream(websocket) 
    """
    camera = Camera(camera_ip)
    await camera.webrtc_stream(websocket)

@app.get("/", response_model=GenericResponse)
async def root():
    return GenericResponse(success=True, data="Welcome to the IP Camera API")

@app.post("/connections/{camera_ip}", response_model=GenericResponse)
async def create_connection(camera_ip: str):
    db = DatabaseManager.load_db()
    if any(c["ip"] == camera_ip for c in db):
        raise HTTPException(status_code=400, detail="Connection with this IP already exists")

    camera = Camera(camera_ip)

    try:
        device_info = camera.test_connection()
    except HTTPException as e:
        return GenericResponse(success=False, data=str(e.detail))
    else:
        device_info.status = CameraStatus.ACTIVE
        db.append(device_info.model_dump())
        DatabaseManager.save_db(db)
        return GenericResponse(success=True, data=device_info)

@app.delete("/connections/{camera_ip}", response_model=GenericResponse)
async def delete_connection(camera_ip: str):
    db = DatabaseManager.load_db()
    db = [c for c in db if c["ip"] != camera_ip]
    DatabaseManager.save_db(db)
    return GenericResponse(success=True, data="Connection deleted")

@app.get("/connections", response_model=GenericResponse)
async def list_connections():
    db = DatabaseManager.load_db()
    if not db:
        return GenericResponse(success=True, data=[])
    devices = [DeviceInfo(**item) for item in db]
    return GenericResponse(success=True, data=devices)

@app.post("/connections/{camera_ip}/test", response_model=GenericResponse)
async def test_connection_endpoint(camera_ip: str):

    try:
        device_info = check_camera_working(camera_ip)
    except HTTPException as e:
        return GenericResponse(success=False, data=str(e.detail))
    else:
        return GenericResponse(success=True, data=device_info)

@app.get("/connections/test_all", response_model=GenericResponse)
async def test_all_connections():
    db = DatabaseManager.load_db()
    if not db:
        return GenericResponse(success=True, data="No connections available to test")

    results = []
    for camera_data in db:
        camera_ip = camera_data["ip"]
        try:
            check_camera_working(camera_ip)
        except:
            new_device_info = DeviceInfo(**camera_data)
            new_device_info.status = CameraStatus.INACTIVE
            results.append(new_device_info)
        else:
            new_device_info = DeviceInfo(**camera_data)
            new_device_info.status = CameraStatus.ACTIVE
            results.append(new_device_info)

    return GenericResponse(success=True, data=results)

@app.get("/capture/{camera_ip}", response_class=StreamingResponse)
async def capture_image(camera_ip: str):
    check_camera_ip_exists_and_active(camera_ip)

    try:
        device_info = check_camera_working(camera_ip)
    except HTTPException as e:
        return GenericResponse(success=False, data=str(e.detail))
    else:
        try:
            camera = Camera(camera_ip)
            image_data = camera.capture_image()
            return StreamingResponse(io.BytesIO(image_data), media_type="image/jpeg")
        except HTTPException as e:
            logger.error(f"Failed to capture image from {camera_ip}: {str(e)}")
            return GenericResponse(success=False, data=str(e.detail))

@app.get("/capture", response_model=GenericResponse)
async def capture_images():
    db = DatabaseManager.load_db()
    if not db:
        return GenericResponse(success=True, data=[])

    captured_images = []
    for camera_data in db:
        camera_ip = camera_data["ip"]
        camera = Camera(camera_ip)

        check_camera_ip_exists_and_active(camera_ip)

        try:
            device_info = check_camera_working(camera_ip)
        except HTTPException as e:
            return GenericResponse(success=False, data=str(e.detail))
        else:
            try:
                image_data = camera.capture_image()
                base64_image = base64.b64encode(image_data).decode('utf-8')
                captured_images.append({"ip": camera_ip, "data": base64_image})
            except HTTPException as e:
                logger.error(f"Failed to capture image from {camera_ip}: {str(e)}")
                captured_images.append({"ip": camera_ip, "data": None})

    return GenericResponse(success=True, data=captured_images)

def check_camera_ip_exists(camera_ip: str):
    db = DatabaseManager.load_db()
    if not any(c["ip"] == camera_ip for c in db):
        raise HTTPException(status_code=400, detail="Connection with this IP does not exist")

def check_camera_ip_exists_and_active(camera_ip: str):
    db = DatabaseManager.load_db()
    if not any(c["ip"] == camera_ip for c in db):
        raise HTTPException(status_code=400, detail="Connection with this IP does not exist")
    for c in db:
        if c["ip"] == camera_ip and c["status"] == CameraStatus.INACTIVE:
            raise HTTPException(status_code=400, detail="Connection with this IP is inactive")

def check_camera_working(camera_ip: str) -> DeviceInfo:
    camera = Camera(camera_ip)

    try:
        device_info = camera.test_connection()
    except HTTPException as e:
        db = DatabaseManager.load_db()
        camera_data = next((c for c in db if c["ip"] == camera_ip), None)
        if camera_data:
            camera_data["status"] = CameraStatus.INACTIVE
            DatabaseManager.save_db(db)
        raise HTTPException(status_code=400, detail=str(e.detail))
    else:
        # if test connection is successful, update device status to active
        db = DatabaseManager.load_db()
        camera_data = next((c for c in db if c["ip"] == camera_ip), None)
        if camera_data:
            camera_data["status"] = CameraStatus.ACTIVE
            DatabaseManager.save_db(db)
            return camera_data
        return device_info

if __name__ == "__main__":
    import uvicorn
    # if json db file does not exist, create it
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump([], f)
    
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=logging_config)
