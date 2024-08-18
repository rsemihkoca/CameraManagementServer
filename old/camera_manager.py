import logging
import queue
from old.camera_producer import CameraProducer
from rabbitmq_consumer import RabbitMQConsumer
from config import QUEUE_SIZE

logger = logging.getLogger(__name__)

import sys


class CameraManager:
    def __init__(self):
        self.shared_queue = queue.Queue(maxsize=QUEUE_SIZE)
        try:
            self.producer = CameraProducer(self.shared_queue)
        except Exception as e:
            logger.error(f"Failed to initialize CameraManager: {e}")
            sys.exit(1)
        try:
            self.consumer = RabbitMQConsumer(self.shared_queue)
        except Exception as e:
            logger.error(f"Failed to initialize RabbitMQConsumer: {e}")
            self.producer.stop()
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

    # manager = CameraManager()
    # try:
    #     if not manager.producer.db:
    #         logger.error("No camera connections found. Exiting.")
    #     else:
    #         manager.start()
    #         while True:
    #             time.sleep(1)
    # except KeyboardInterrupt:
    #     logger.info("Keyboard interrupt received. Shutting down...")
    #     manager.stop()
    # except Exception as e:
    #     logger.error(f"An unexpected error occurred: {e}")
    #     manager.stop()
    # finally:
    #     import uvicorn
    #     uvicorn.run(app, host="0.0.0.0", port=8000)
    #     logger.info("Application shutdown complete.")
