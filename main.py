import io

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json
import requests
import os
from typing import List
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv
import xml.etree.ElementTree as ET

from starlette.responses import StreamingResponse

load_dotenv()
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")
app = FastAPI()

# Pydantic model for camera connection
class CameraConnection(BaseModel):
    ip: str

# Pydantic model for device info
class DeviceInfo(BaseModel):
    ip: str
    serialNumber: str
    deviceName: str
    model: str
    firmwareVersion: str

# JSON file to store camera connections
DB_FILE = "camera_connections.json"

# Helper functions
def load_db():
    try:
        with open(DB_FILE, "r") as f:
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

def test_connection(camera: CameraConnection) -> DeviceInfo:
    url = f"http://{camera.ip}/ISAPI/System/deviceInfo"
    auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
    try:
        response = requests.get(url, auth=auth, timeout=5)
        response.raise_for_status()
        device_info_xml = response.text
        device_info = parse_device_info_xml(device_info_xml, camera.ip)
        return device_info
    except requests.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e)}")

# Endpoints
@app.post("/connections", response_model=DeviceInfo)
async def create_connection(camera: CameraConnection):
    db = load_db()
    if any(c["ip"] == camera.ip for c in db):
        raise HTTPException(status_code=400, detail="Connection with this IP already exists")

    # Test connection
    device_info = test_connection(camera)

    # Save connection
    db.append(device_info.model_dump())
    save_db(db)

    return device_info

@app.delete("/connections/{camera_ip}")
async def delete_connection(camera_ip: str):
    db = load_db()
    db = [c for c in db if c["ip"] != camera_ip]
    save_db(db)
    return {"message": "Connection deleted successfully"}

@app.get("/connections", response_model=List[DeviceInfo])
async def list_connections():
    return load_db()

@app.post("/connections/test", response_model=DeviceInfo)
async def test_connection_endpoint(camera: CameraConnection):
    return test_connection(camera)


@app.get("/capture/{camera_ip}")
async def capture_image(camera_ip: str):
    db = load_db()
    camera = next((c for c in db if c["ip"] == camera_ip), None)

    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found in the database")

    # Test connection
    try:
        test_connection(CameraConnection(ip=camera_ip))
    except HTTPException as e:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e.detail)}")

    # Capture image
    url = f"http://{camera_ip}/ISAPI/Streaming/channels/1/picture"
    auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)

    try:
        response = requests.get(url, auth=auth, timeout=10)
        response.raise_for_status()
        image_data = response.content

        return StreamingResponse(io.BytesIO(image_data), media_type="image/jpeg")
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to capture image: {str(e)}")

@app.get("/stream/{camera_ip}")
async def stream_video(camera_ip: str):
    db = load_db()
    camera = next((c for c in db if c["ip"] == camera_ip), None)

    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found in the database")

    # Test connection
    try:
        test_connection(CameraConnection(ip=camera_ip))
    except HTTPException as e:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e.detail)}")

    # Stream video
    url = f"http://{camera_ip}/ISAPI/Streaming/channels/1/httpPreview"
    auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)

    def stream():
        try:
            with requests.get(url, auth=auth, stream=True, timeout=10) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=8192):
                    yield chunk
        except requests.RequestException as e:
            raise HTTPException(status_code=500, detail=f"Failed to stream video: {str(e)}")

    return StreamingResponse(stream(), media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
