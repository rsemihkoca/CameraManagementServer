import threading
import time
import logging
import queue
import requests
import json
from requests.auth import HTTPDigestAuth
from typing import List, Dict
from config import CAMERA_USERNAME, CAMERA_PASSWORD, DB_FILE, CAPTURE_INTERVAL

logger = logging.getLogger(__name__)

class CameraProducer:
    def __init__(self, shared_queue: queue.Queue):
        self.shared_queue = shared_queue
        self.db = self._load_db()
        self.running = False
        self.capture_thread = None
        self._verify_camera_connections()

    def _load_db(self) -> List[Dict]:
        try:
            with open(DB_FILE, "r") as f:
                logger.info("Camera database loaded successfully.")
                return json.load(f)
        except FileNotFoundError:
            logger.error("Camera database file not found.")
            return []

    def _verify_camera_connections(self):
        for camera in self.db:
            if self._test_connection(camera['ip']):
                logging.info(f"Camera at {camera['ip']} is reachable and authenticated.")
            else:
                logging.warning(f"Camera at {camera['ip']} is not reachable or authentication failed.")
        logger.info("Camera connections verified.")

    def _test_connection(self, camera_ip: str) -> bool:
        url = f"http://{camera_ip}/ISAPI/System/deviceInfo"
        auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
        try:
            response = requests.get(url, auth=auth, timeout=5)
            response.raise_for_status()
            logger.info(f"Successfully connected to camera at {camera_ip}")
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to connect to camera at {camera_ip}: {e}")
            return False

    def _capture_image(self, camera_ip: str) -> bytes:
        url = f"http://{camera_ip}/ISAPI/Streaming/channels/1/picture"
        auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
        try:
            response = requests.get(url, auth=auth, timeout=10)
            response.raise_for_status()
            logger.info(f"Image captured successfully from {camera_ip}")
            return response.content
        except requests.RequestException as e:
            logger.error(f"Failed to capture image from {camera_ip}: {e}")
            return None

    def _process_camera(self, camera: Dict):
        image_data = self._capture_image(camera['ip'])
        if image_data:
            try:
                self.shared_queue.put({'ip': camera['ip'], 'data': image_data}, timeout=1)
                logger.info(f"Image from {camera['ip']} added to queue.")
            except queue.Full:
                logger.warning(f"Queue is full. Discarding image from {camera['ip']}.")

    def _capture_images(self):
        while self.running:
            for camera in self.db:
                if self._test_connection(camera['ip']):
                    self._process_camera(camera)
            time.sleep(CAPTURE_INTERVAL)

    def start(self):
        self.running = True
        self.capture_thread = threading.Thread(target=self._capture_images)
        self.capture_thread.start()
        logger.info("CameraProducer started.")

    def stop(self):
        self.running = False
        if self.capture_thread:
            self.capture_thread.join()
        logger.info("CameraProducer stopped.")