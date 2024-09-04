import io
import logging
import json
import requests
import os
import base64
from typing import List, Union
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
import subprocess
from starlette.responses import StreamingResponse, JSONResponse
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer
from config import DB_FILE
from enum import Enum
from colorama import init, Fore, Style
from contextlib import asynccontextmanager
from log_config import setup_logging, logging_config
from fastapi import FastAPI, HTTPException, WebSocket
from aiortc import RTCSessionDescription

import sys

setup_logging()
load_dotenv()
CAMERA_API_VERSION = os.environ.get("CAMERA_API_VERSION")
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")

logger = logging.getLogger("app")  # Replace with your logger name

logger.info(f"API version: {CAMERA_API_VERSION}")


@asynccontextmanager
async def lifespan(app: FastAPI):          
    # Prepare Database
    DatabaseManager.check_connections()
    yield
    # Shutdown


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
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

    def stream_video(self):
        # RTSP URL for the camera stream
        # This format is common for many IP cameras, but might need adjustment for your specific model
        url = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{self.ip}/h264/ch1/main/av_stream"

        # FFmpeg command to capture and transcode the video stream
        command = [
            'ffmpeg',
            '-i', url,  # Input URL
            '-f', 'mpegts',  # Output format (MPEG transport stream)
            '-codec:v', 'libx264',  # Video codec (H.264 for high quality)
            '-preset', 'ultrafast',  # Encoding preset (reduces CPU usage)
            '-tune', 'zerolatency',  # Tuning for low-latency streaming
            '-s', '1920x1080',  # Resolution (Full HD)
            '-b:v', '5M',  # Video bitrate (5 Mbps for high quality)
            '-maxrate', '5M',  # Maximum bitrate
            '-bufsize', '10M',  # Buffer size (2x maxrate for smoother quality)
            '-g', '25',  # Keyframe interval (2 seconds at 30 fps)
            '-codec:a', 'aac',  # Audio codec (AAC for good quality)
            '-b:a', '192k',  # Audio bitrate
            '-ar', '48000',  # Audio sample rate
            '-ac', '2',  # Number of audio channels (stereo)
            '-muxdelay', '0.001',  # Reduces latency in muxing
            '-'  # Output to stdout
        ]

        # Start the FFmpeg process
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def generate():
            """Generator function to yield video data"""
            while True:
                # Read 4K chunks of data from the FFmpeg process
                data = process.stdout.read(4096)
                if not data:
                    break
                yield data

        return generate()
    
    async def create_webrtc_offer(self):
        pc = RTCPeerConnection()
        
        # Use the highest quality stream available
        url = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{self.ip}/h264/ch1/main/av_stream"
        
        # Configure MediaPlayer with high-quality options
        player = MediaPlayer(url, format='rtsp', options={
            'rtsp_transport': 'tcp',  # Use TCP for more reliable streaming
            'buffer_size': '10240k',  # Increase buffer size for smoother playback
            'max_delay': '0',  # Minimize delay
            'fflags': 'nobuffer',  # Reduce buffering
            'flags': 'low_delay',  # Further reduce delay
            'framedrop': '1',  # Allow frame dropping to maintain sync
            'vstats': '1',  # Enable video statistics
        })
        
        # Add video track with high-quality constraints
        video_sender = pc.addTrack(player.video)
        video_sender.setCodecPreferences([
            RTCRtpCodecParameters(mimeType='video/H264', clockRate=90000, payloadType=96,
                                  parameters={'profile-level-id': '42e01f', 'packetization-mode': '1'}),
        ])

        # Add audio track if available
        if player.audio:
            audio_sender = pc.addTrack(player.audio)
            audio_sender.setCodecPreferences([
                RTCRtpCodecParameters(mimeType='audio/opus', clockRate=48000, channels=2, payloadType=111),
            ])

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def handle_webrtc_answer(self, answer: RTCSessionDescription):
        pc = RTCPeerConnection()
        await pc.setRemoteDescription(answer)
        
        @pc.on("icecandidate")
        def on_icecandidate(candidate):
            if candidate:
                print(f"New ICE candidate: {candidate.sdp}")

        return pc


    def capture_image(self) -> bytes:
        url = f"http://{self.ip}/ISAPI/Streaming/channels/1/picture"
        try:
            response = requests.get(url, auth=self.auth, timeout=10)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            raise HTTPException(status_code=500, detail=f"Failed to capture image: {str(e)}")

class DatabaseManager:
    @staticmethod
    def load_db():
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    @staticmethod
    def save_db(data):
        with open(DB_FILE, "w") as f:
            json.dump(data, f)

    @staticmethod
    def check_connections():
        db = DatabaseManager.load_db()
        connections = []
        for camera_data in db:
            camera = Camera(camera_data["ip"])
            # if test connection fails, update device status to inactive
            try:
                camera.test_connection()
            except HTTPException:
                camera_data["status"] = CameraStatus.INACTIVE
            else:
                camera_data["status"] = CameraStatus.ACTIVE
            connections.append(camera_data)
        DatabaseManager.save_db(connections)



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

    # check_camera_ip_exists_and_active(camera_ip)
    #check_camera_ip_exists(camera_ip)

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


@app.get("/stream/{camera_ip}")
async def stream_video(camera_ip: str):
    """
    Endpoint to stream video from a specific camera
    :param camera_ip: IP address of the camera
    :return: StreamingResponse with the video stream
    """
    # Check if the camera exists and is active
    check_camera_ip_exists_and_active(camera_ip)

    try:
        # Test the camera connection
        device_info = check_camera_working(camera_ip)
    except HTTPException as e:
        # If the connection test fails, return an error response
        return GenericResponse(success=False, data=str(e.detail))
    else:
        try:
            # Create a Camera instance and start streaming
            camera = Camera(camera_ip)
            return StreamingResponse(
                camera.stream_video(),
                media_type="video/mp2t",
                headers={
                    'Content-Disposition': f'inline; filename="stream_{camera_ip}.ts"',
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Expires': '0',
                }
            )
        except Exception as e:
            # Log any errors that occur during streaming
            logger.error(f"Failed to stream video from {camera_ip}: {str(e)}")
            return GenericResponse(success=False, data=str(e))

@app.websocket("/webrtc/{camera_ip}")
async def webrtc_stream(websocket: WebSocket, camera_ip: str):
    await websocket.accept()
    
    check_camera_ip_exists_and_active(camera_ip)
    pc = None

    try:
        camera = Camera(camera_ip)
        offer = await camera.create_webrtc_offer()
        await websocket.send_json(offer)

        answer = await websocket.receive_json()
        pc = await camera.handle_webrtc_answer(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

        while True:
            # Keep the connection alive
            await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"WebRTC streaming error for {camera_ip}: {str(e)}")
        await websocket.close()
    finally:
        # Cleanup
        if pc:
            await pc.close()
            
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
