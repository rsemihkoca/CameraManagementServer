import logging
import queue
import json
import time
import threading
import pika
from config import RABBITMQ_HOST, RABBITMQ_QUEUE, RABBITMQ_USERNAME, RABBITMQ_PASSWORD

logger = logging.getLogger(__name__)

class RabbitMQConsumer:
    def __init__(self, shared_queue: queue.Queue):
        self.shared_queue = shared_queue
        self.connection = None
        self.channel = None
        self.running = False
        self.consumer_thread = None

    def _connect(self):
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)
            parameters = pika.ConnectionParameters(RABBITMQ_HOST, credentials=credentials)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            # check if the queue exists, if not create it
            if self.channel.queue_declare(queue=RABBITMQ_QUEUE, passive=True):
                logger.info(f"Connected to RabbitMQ and found queue '{RABBITMQ_QUEUE}'")
            else:
                self.channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
                logger.info(f"Connected to RabbitMQ and declared queue '{RABBITMQ_QUEUE}'")

        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ server: {e}")
            raise ConnectionError(f"Failed to connect to RabbitMQ server: {e}")

    def _disconnect(self):
        if self.connection:
            self.connection.close()
            logger.info("Disconnected from RabbitMQ")

    def _publish_message(self, message):
        try:
            self.channel.basic_publish(
                exchange='',
                routing_key=RABBITMQ_QUEUE,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            logger.info(f"Sent message to '{RABBITMQ_QUEUE}': {message['ip']}")
        except Exception as e:
            logger.error(f"Failed to publish message: {e}")

    def _consume(self):
        self._connect()
        while self.running:
            try:
                message = self.shared_queue.get(timeout=1)
                self._publish_message(message)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in consumer: {e}")
                time.sleep(5)  # Wait before reconnecting
                self._connect()

    def start(self):
        self.running = True
        self.consumer_thread = threading.Thread(target=self._consume)
        self.consumer_thread.start()
        logger.info("RabbitMQConsumer started.")

    def stop(self):
        self.running = False
        if self.consumer_thread:
            self.consumer_thread.join()
        self._disconnect()
        logger.info("RabbitMQConsumer stopped.")