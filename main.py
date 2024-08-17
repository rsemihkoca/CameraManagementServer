import io
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json
import requests
import os
from typing import List, Union
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
import time
from starlette.responses import StreamingResponse, JSONResponse
from config import DB_FILE
from camera_manager import CameraManager

load_dotenv()
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")
app = FastAPI()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Pydantic models
class DeviceInfo(BaseModel):
    ip: str
    serialNumber: str
    deviceName: str
    model: str
    firmwareVersion: str

class GenericResponse(BaseModel):
    success: bool
    data: Union[DeviceInfo, List[DeviceInfo], None] = None
    content: Union[str, None] = None

# Helper functions
def load_db():
    try:
        with open(DB_FILE, "rb") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f)

def parse_device_info_xml(xml_string: str, ip: str) -> DeviceInfo:
    ns = {"ns": "http://www.hikvision.com/ver20/XMLSchema"}
    root = ET.fromstring(xml_string)
    return DeviceInfo(
        ip=ip,
        serialNumber=root.find("ns:serialNumber", ns).text,
        deviceName=root.find("ns:deviceName", ns).text,
        model=root.find("ns:model", ns).text,
        firmwareVersion=root.find("ns:firmwareVersion", ns).text
    )

def test_connection(camera_ip) -> DeviceInfo:
    url = f"http://{camera_ip}/ISAPI/System/deviceInfo"
    auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
    try:
        response = requests.get(url, auth=auth, timeout=5)
        response.raise_for_status()
        device_info_xml = response.text
        device_info = parse_device_info_xml(device_info_xml, camera_ip)
        return device_info
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e)}")

# Endpoints
@app.post("/connections/{camera_ip}", response_model=GenericResponse)
async def create_connection(camera_ip: str):
    db = load_db()
    if any(c["ip"] == camera_ip for c in db):
        raise HTTPException(status_code=400, detail="Connection with this IP already exists")

    device_info = test_connection(camera_ip)

    db.append(device_info.dict())
    save_db(db)

    return GenericResponse(success=True, data=device_info)

@app.delete("/connections/{camera_ip}", response_model=GenericResponse)
async def delete_connection(camera_ip: str):
    db = load_db()
    db = [c for c in db if c["ip"] != camera_ip]
    save_db(db)
    return GenericResponse(success=True, content="Connection deleted successfully")

@app.get("/connections", response_model=GenericResponse)
async def list_connections():
    db = load_db()
    if not db:
        return GenericResponse(success=True, data=[])
    devices = [DeviceInfo(**item) for item in db]
    return GenericResponse(success=True, data=devices)

@app.post("/connections/{camera_ip}/test", response_model=GenericResponse)
async def test_connection_endpoint(camera_ip: str):
    device_info = test_connection(camera_ip)
    return GenericResponse(success=True, data=device_info)

@app.get("/capture/{camera_ip}", response_class=StreamingResponse)
async def capture_image(camera_ip: str):
    db = load_db()
    camera = next((c for c in db if c["ip"] == camera_ip), None)

    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found in the database")

    try:
        test_connection(camera_ip)
    except HTTPException as e:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e.detail)}")

    url = f"http://{camera_ip}/ISAPI/Streaming/channels/1/picture"
    auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)

    try:
        response = requests.get(url, auth=auth, timeout=10)
        response.raise_for_status()
        image_data = response.content

        return StreamingResponse(io.BytesIO(image_data), media_type="image/jpeg")
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to capture image: {str(e)}")

if __name__ == "__main__":
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump([], f)  # Ensure DB file is an empty list
    manager = CameraManager()
    try:
        if not manager.producer.db:
            logger.error("No camera connections found. Exiting.")
        else:
            manager.start()
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down...")
        manager.stop()
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        manager.stop()
    finally:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
        logger.info("Application shutdown complete.")
