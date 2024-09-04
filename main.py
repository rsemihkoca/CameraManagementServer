import io
import logging
import json
import requests
import os
import base64
from typing import List, Union
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from requests.auth import HTTPDigestAuth
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
import subprocess
from starlette.responses import StreamingResponse, JSONResponse
import asyncio
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate

from aiortc.contrib.media import MediaPlayer
from config import DB_FILE
from enum import Enum
from contextlib import asynccontextmanager
from log_config import setup_logging, logging_config

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



class WebRTCStreamer:
    def __init__(self, camera_ip, username, password):
        self.camera_ip = camera_ip
        self.username = username
        self.password = password
        self.pc = None
        self.ws = None
        self.closed = False
        self.ice_candidates = []

    async def create_offer(self):
        if self.pc:
            await self.close_peer_connection()
        
        self.pc = RTCPeerConnection()
        
        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info(f"ICE connection state is {self.pc.iceConnectionState}")
            if self.pc.iceConnectionState == "failed":
                await self.close()

        @self.pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            logger.info(f"ICE gathering state is {self.pc.iceGatheringState}")

        @self.pc.on("icecandidate")
        def on_icecandidate(candidate):
            if candidate:
                self.ice_candidates.append(candidate)

        url = f"rtsp://{self.username}:{self.password}@{self.camera_ip}/h264/ch1/main/av_stream"
        
        player = MediaPlayer(url, format='rtsp', options={
            'rtsp_transport': 'tcp',
            'buffer_size': '20480k',
            'max_delay': '0',
            'fflags': 'nobuffer',
            'flags': 'low_delay',
            'framedrop': '1',
            'vstats': '1',
            'probesize': '10M',
            'analyzeduration': '10M',
            'preset': 'ultrafast',
            'tune': 'zerolatency',
        })
        
        self.pc.addTrack(player.video)

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        return {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}

    async def handle_answer(self, answer):
        if not self.pc:
            logger.warning("Received answer but peer connection is not initialized")
            return
        if self.pc.signalingState == "stable":
            logger.warning("Peer connection is already in 'stable' state. Ignoring answer.")
            return
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        
        # Send any gathered ICE candidates after setting remote description
        for candidate in self.ice_candidates:
            await self.send_ice_candidate(candidate)
        self.ice_candidates.clear()

    async def add_ice_candidate(self, candidate):
        if not self.pc:
            logger.warning("Received ICE candidate but peer connection is not initialized")
            return
        if not self.pc.remoteDescription:
            logger.warning("Received ICE candidate but remote description is not set")
            return
        try:
            await self.pc.addIceCandidate(RTCIceCandidate(**candidate))
        except Exception as e:
            logger.error(f"Error adding ICE candidate: {str(e)}")

    async def send_ice_candidate(self, candidate):
        if self.ws and not self.closed:
            await self.ws.send_json({
                "type": "candidate",
                "data": {
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex
                }
            })

    async def close_peer_connection(self):
        if self.pc:
            await self.pc.close()
            self.pc = None

    async def close(self):
        self.closed = True
        await self.close_peer_connection()
        if self.ws:
            await self.ws.close()
            self.ws = None

async def webrtc_stream(websocket: WebSocket, camera_ip: str, username: str, password: str):
    await websocket.accept()
    
    streamer = WebRTCStreamer(camera_ip, username, password)
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
        logger.error(f"WebRTC streaming error for {camera_ip}: {str(e)}")
    finally:
        await streamer.close()

# In your FastAPI app
@app.websocket("/webrtc/{camera_ip}")
async def webrtc_endpoint(websocket: WebSocket, camera_ip: str):
    await webrtc_stream(websocket, camera_ip, CAMERA_USERNAME, CAMERA_PASSWORD)
    
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
