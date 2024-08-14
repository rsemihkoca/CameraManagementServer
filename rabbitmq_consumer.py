import logging
import queue
import json
import time
import threading
import pika
import base64
from config import RABBITMQ_HOST, RABBITMQ_QUEUE, RABBITMQ_USERNAME, RABBITMQ_PASSWORD

logger = logging.getLogger(__name__)

import sys

class RabbitMQConsumer:
    def __init__(self, shared_queue: queue.Queue):
        self.shared_queue = shared_queue
        self.connection = None
        self.channel = None
        self.running = False
        self.consumer_thread = None
        try:
            self._connect()
        except ConnectionError as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            sys.exit(1)

    def _connect(self):
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()

            credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)
            parameters = pika.ConnectionParameters(RABBITMQ_HOST, credentials=credentials)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()

            if self._check_queue_exists():
                logger.info(f"Connected to RabbitMQ and found queue '{RABBITMQ_QUEUE}'")
            else:
                self.channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
                logger.info(f"Connected to RabbitMQ and declared queue '{RABBITMQ_QUEUE}'")

            return True
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ server: {e}")
            return False

    def _check_queue_exists(self) -> bool:
        try:
            self.channel.queue_declare(queue=RABBITMQ_QUEUE, passive=True)
            return True
        except pika.exceptions.ChannelClosedByBroker:
            # Queue doesn't exist, we'll create it
            return False
        except Exception as e:
            logger.error(f"Error checking queue existence: {e}")
            return False

    def _disconnect(self):
        if self.connection and self.connection.is_open:
            self.connection.close()
            logger.info("Disconnected from RabbitMQ")

    def _publish_message(self, message):
        try:
            if not self.channel or self.channel.is_closed:
                if not self._connect():
                    raise ConnectionError("Failed to reconnect to RabbitMQ")

            encoded_data = base64.b64encode(message['data']).decode('utf-8')
            json_message = {
                'ip': message['ip'],
                'data': encoded_data
            }

            self.channel.basic_publish(
                exchange='',
                routing_key=RABBITMQ_QUEUE,
                body=json.dumps(json_message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            logger.info(f"Sent message to '{RABBITMQ_QUEUE}': {message['ip']}")
        except Exception as e:
            logger.error(f"Failed to publish message: {e}")
            raise

    def _consume(self):
        while self.running:
            try:
                if not self.channel or self.channel.is_closed:
                    if not self._connect():
                        time.sleep(5)  # Wait before retrying connection
                        continue

                message = self.shared_queue.get(timeout=1, block=False)
                self._publish_message(message)
                self.shared_queue.task_done()
                logger.info(f"Successfully processed and removed message from {message['ip']}")
            except queue.Empty:
                continue
            except pika.exceptions.AMQPConnectionError:
                logger.error("AMQP Connection Error. Attempting to reconnect...")
                self._connect()
            except Exception as e:
                logger.error(f"Error in consumer: {e}")
                if 'message' in locals():
                    self.shared_queue.put(message)
                    logger.info(f"Put message from {message['ip']} back in the queue due to error")
                time.sleep(5)  # Wait before retrying

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