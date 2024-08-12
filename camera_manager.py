import logging
import queue
from camera_producer import CameraProducer
from rabbitmq_consumer import RabbitMQConsumer
from config import QUEUE_SIZE

logger = logging.getLogger(__name__)

import sys

class CameraManager:
    def __init__(self):
        self.shared_queue = queue.Queue(maxsize=QUEUE_SIZE)
        try:
            self.producer = CameraProducer(self.shared_queue)
            self.consumer = RabbitMQConsumer(self.shared_queue)
        except Exception as e:
            logger.error(f"Failed to initialize CameraManager: {e}")
            sys.exit(1)

    def start(self):
        logger.info("Starting CameraManager...")
        try:
            self.producer.start()
            self.consumer.start()
        except Exception as e:
            logger.error(f"Failed to start CameraManager: {e}")
            self.stop()
            sys.exit(1)

    def stop(self):
        logger.info("Stopping CameraManager...")
        self.producer.stop()
        self.consumer.stop()