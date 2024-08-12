import logging
import queue
from camera_producer import CameraProducer
from rabbitmq_consumer import RabbitMQConsumer
from config import QUEUE_SIZE

logger = logging.getLogger(__name__)

class CameraManager:
    def __init__(self):
        self.shared_queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.producer = CameraProducer(self.shared_queue)
        self.consumer = RabbitMQConsumer(self.shared_queue)

    def start(self):
        logger.info("Starting CameraManager...")
        self.producer.start()
        self.consumer.start()

    def stop(self):
        logger.info("Stopping CameraManager...")
        self.producer.stop()
        self.consumer.stop()