import os
import time
import threading
import logging
import queue
import requests
import json
from requests.auth import HTTPDigestAuth
from typing import List, Dict
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables
from dotenv import load_dotenv
load_dotenv()
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")

# Constants
DB_FILE = "camera_connections.json"
CAPTURE_INTERVAL = 300  # 5 minutes in seconds

class CameraProducer:
    def __init__(self, queue_size: int = 10):
        self.queue = queue.Queue(maxsize=queue_size)
        self.db = self.load_db()
        self.lock = threading.Lock()
        self.running = True
        self.verify_camera_connections()

    def load_db(self) -> List[Dict]:
        try:
            with open(DB_FILE, "r") as f:
                logging.info("Database loaded successfully.")
                return json.load(f)
        except FileNotFoundError:
            logging.error("Database file not found.")
            return []

    def verify_camera_connections(self):
        valid_cameras = []
        for camera in self.db:
            if self.test_connection(camera['ip']):
                valid_cameras.append(camera)
            else:
                logging.warning(f"Camera at {camera['ip']} is not reachable or authentication failed.")
        self.db = valid_cameras
        logging.info(f"{len(valid_cameras)} cameras verified and ready for capturing.")

    def test_connection(self, camera_ip: str) -> bool:
        url = f"http://{camera_ip}/ISAPI/System/deviceInfo"
        auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
        try:
            response = requests.get(url, auth=auth, timeout=5)
            response.raise_for_status()
            logging.info(f"Successfully connected to camera at {camera_ip}")
            return True
        except requests.RequestException as e:
            logging.error(f"Failed to connect to camera at {camera_ip}: {e}")
            return False

    def capture_image(self, camera_ip: str) -> bytes:
        url = f"http://{camera_ip}/ISAPI/Streaming/channels/1/picture"
        auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
        try:
            response = requests.get(url, auth=auth, timeout=10)
            response.raise_for_status()
            logging.info(f"Image captured successfully from {camera_ip}")
            return response.content
        except requests.RequestException as e:
            logging.error(f"Failed to capture image from {camera_ip}: {e}")
            return None

    def process_camera(self, camera: Dict):
        image_data = self.capture_image(camera['ip'])
        if image_data:
            try:
                self.queue.put({'ip': camera['ip'], 'data': image_data}, timeout=CAPTURE_INTERVAL)
                logging.info(f"Image from {camera['ip']} added to queue.")
            except queue.Full:
                logging.warning(f"Queue is full. Waiting to add image from {camera['ip']}.")

    def capture_images(self):
        while self.running:
            for camera in self.db:
                self.process_camera(camera)
            time.sleep(CAPTURE_INTERVAL)

    def start(self):
        self.capture_thread = threading.Thread(target=self.capture_images)
        self.capture_thread.start()
        logging.info("CameraProducer started.")

    def stop(self):
        self.running = False
        self.capture_thread.join()
        logging.info("CameraProducer stopped.")

# Example usage
if __name__ == "__main__":
    producer = CameraProducer(queue_size=10)
    try:
        producer.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        producer.stop()
