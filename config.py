import os
from dotenv import load_dotenv

load_dotenv()

# Camera settings
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")
DB_FILE = "camera_connections.json"
CAPTURE_INTERVAL = 300  # 5 minutes in seconds

# Queue settings
QUEUE_SIZE = 100

# RabbitMQ settings
RABBITMQ_HOST = 'localhost'
RABBITMQ_USERNAME = 'user'
RABBITMQ_PASSWORD = 'password'
RABBITMQ_QUEUE = 'camera_images'