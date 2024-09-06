
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer
from fastapi import FastAPI, HTTPException, WebSocket
from typing import List, Union, Dict, Any, Generator
from log_config import setup_logging
from dotenv import load_dotenv
import logging
import os
setup_logging()
load_dotenv()
CAMERA_API_VERSION = os.environ.get("CAMERA_API_VERSION")
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")

logger = logging.getLogger("app")
logger.info(f"API version: {CAMERA_API_VERSION}")


class WebRTCStreamer:
    def __init__(self, camera_ip: str, username: str, password: str):
        self.camera_ip = camera_ip
        self.username = username
        self.password = password
        self.pc: RTCPeerConnection = None
        self.ws: WebSocket = None
        self.closed = False
        self.ice_candidates: List[RTCIceCandidate] = []

    async def create_offer(self) -> Dict[str, Any]:
        if self.pc:
            await self.close_peer_connection()
        
        self.pc = RTCPeerConnection()
        
        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            logger.info(f"ICE connection state is {self.pc.iceConnectionState}")
            if self.pc.iceConnectionState == "failed":
                logger.warning("ICE connection failed. Closing connection.")
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
            'loglevel': 'fatal',
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
            'vf': 'scale=1920:1080',  # 4MP resolution (16:9 aspect ratio)
            'b:v': '8M',  # Increased bitrate for higher quality
            'maxrate': '10M',
            'bufsize': '20M',
            'g': '20',
            'keyint_min': '30', 
            'sc_threshold': '0',
        })
        
        self.pc.addTrack(player.video)

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        return {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}

    async def handle_answer(self, answer: Dict[str, Any]) -> None:
        if not self.pc:
            logger.warning("Received answer but peer connection is not initialized")
            return
        if self.pc.signalingState == "stable":
            logger.warning("Peer connection is already in 'stable' state. Ignoring answer.")
            return
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
        
        for candidate in self.ice_candidates:
            await self.send_ice_candidate(candidate)
        self.ice_candidates.clear()

    async def add_ice_candidate(self, candidate: Dict[str, Any]) -> None:
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

    async def send_ice_candidate(self, candidate: RTCIceCandidate) -> None:
        if self.ws and not self.closed:
            await self.ws.send_json({
                "type": "candidate",
                "data": {
                    "candidate": candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex
                }
            })

    async def close_peer_connection(self) -> None:
        if self.pc:
            await self.pc.close()
            self.pc = None

    async def close(self) -> None:
        self.closed = True
        await self.close_peer_connection()
        if self.ws:
            await self.ws.close()
            self.ws = None
