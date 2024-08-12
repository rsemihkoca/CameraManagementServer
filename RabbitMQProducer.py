import pika
import json
import logging

class RabbitMQProducer:
    def __init__(self, host='localhost', queues=None):
        self.host = host
        self.queues = queues or ['default']
        self.connection = None
        self.channel = None
        self._setup_logging()
        self._connect()
        self._setup_queues()

    def _setup_logging(self):
        self.logger = logging.getLogger(__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def _connect(self):
        try:
            self.connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.host))
            self.channel = self.connection.channel()
            self.logger.info("Connected to RabbitMQ")
        except Exception as e:
            self.logger.error(f"Failed to connect to RabbitMQ server: {e}")
            raise ConnectionError(f"Failed to connect to RabbitMQ server: {e}")

    def _setup_queues(self):
        try:
            for queue in self.queues:
                self.channel.queue_declare(queue=queue, durable=True)
                self.logger.info(f"Queue '{queue}' has been declared.")
        except Exception as e:
            self.logger.error(f"Failed to declare queue: {e}")
            raise Exception(f"Failed to declare queue: {e}")

    def _disconnect(self):
        if self.connection:
            self.connection.close()
            self.logger.info("Disconnected from RabbitMQ")

    def publish_message(self, message, queue='default'):
        if queue not in self.queues:
            self.logger.error(f"Queue '{queue}' is not in the list of known queues.")
            raise ValueError(f"Queue '{queue}' is not in the list of known queues.")
        if not isinstance(message, (str, bytes)):
            message = json.dumps(message)
        try:
            self.channel.basic_publish(exchange='',
                                       routing_key=queue,
                                       body=message,
                                       properties=pika.BasicProperties(
                                           delivery_mode=2,  # make message persistent
                                       ))
            self.logger.info(f"Sent message to '{queue}': {message}")
        except Exception as e:
            self.logger.error(f"Failed to publish message: {e}")
            raise ConnectionError(f"Failed to publish message: {e}")

    def __del__(self):
        self._disconnect()

