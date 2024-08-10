import asyncio
import socket
import aioping
from scapy.layers.l2 import ARP, Ether, srp
from fastapi import FastAPI, HTTPException
import requests
from pydantic import BaseModel
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = FastAPI()

# Global variable to store camera information
cameras = {}


class CameraConfig(BaseModel):
    username: str
    password: str
    new_ip: str = None
    new_port: int = None


async def scan_network(interface):
    logging.info(f"Starting network scan on interface {interface}")
    try:
        arp = ARP(pdst=f"{interface}/24")
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")
        packet = ether / arp
        result = srp(packet, timeout=3, verbose=0)[0]
        ip_list = [received.psrc for sent, received in result]
        logging.info(f"Network scan complete. Found IPs: {ip_list}")
        return ip_list
    except PermissionError:
        logging.error("Permission denied for network scan")
        raise HTTPException(status_code=403, detail="Permission denied for network scan")
    except Exception as e:
        logging.error(f"Error scanning network: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error scanning network: {str(e)}")


async def ping_host(ip):
    logging.info(f"Pinging IP: {ip}")
    try:
        delay = await aioping.ping(ip, timeout=1)
        logging.info(f"Ping successful: {ip}, delay: {delay}")
        return ip, True, delay
    except TimeoutError:
        logging.info(f"Ping timeout: {ip}")
        return ip, False, None


async def get_camera_info(ip):
    logging.info(f"Getting camera info for IP: {ip}")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((ip, 80))
        if result == 0:
            sock.send(b"GET /System/deviceInfo HTTP/1.1\r\nHost: " + ip.encode() + b"\r\n\r\n")
            response = sock.recv(1024).decode()
            if "Hikvision" in response:
                logging.info(f"Hikvision camera detected at IP: {ip}")
                return ip, "Hikvision camera detected"
        sock.close()
    except Exception as e:
        logging.error(f"Error getting camera info for IP {ip}: {str(e)}")
    return ip, "Not a Hikvision camera or not accessible"


async def scan_cameras():
    interface = "wlp0s20f3"  # Replace with your actual interface
    ip_list = await scan_network(interface)

    ping_tasks = [ping_host(ip) for ip in ip_list]
    ping_results = await asyncio.gather(*ping_tasks)

    active_ips = [ip for ip, is_active, _ in ping_results if is_active]

    camera_tasks = [get_camera_info(ip) for ip in active_ips]
    camera_results = await asyncio.gather(*camera_tasks)

    global cameras
    cameras = {ip: info for ip, info in camera_results if "Hikvision camera detected" in info}
    logging.info(f"Camera scan complete. Found cameras: {cameras}")


@app.on_event("startup")
async def startup_event():
    logging.info("Starting up and scanning for cameras")
    await scan_cameras()


@app.get("/cameras")
async def get_cameras():
    logging.info("Fetching list of cameras")
    return cameras


@app.put("/cameras/{camera_ip}")
async def update_camera_config(camera_ip: str, config: CameraConfig):
    logging.info(f"Updating camera config for IP: {camera_ip}")
    if camera_ip not in cameras:
        logging.warning(f"Camera not found: {camera_ip}")
        raise HTTPException(status_code=404, detail="Camera not found")

    # Here you would typically use a library specific to Hikvision cameras
    # This is a simplified example using requests
    url = f"http://{camera_ip}/config"
    data = {
        "username": config.username,
        "password": config.password,
        "new_ip": config.new_ip,
        "new_port": config.new_port
    }

    try:
        response = requests.put(url, json=data)
        response.raise_for_status()
        logging.info(f"Configuration updated successfully for IP: {camera_ip}")
        return {"message": "Configuration updated successfully"}
    except requests.RequestException as e:
        logging.error(f"Error updating configuration for IP {camera_ip}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cameras/{camera_ip}/capture")
async def capture_photo(camera_ip: str):
    logging.info(f"Capturing photo from camera at IP: {camera_ip}")
    if camera_ip not in cameras:
        logging.warning(f"Camera not found: {camera_ip}")
        raise HTTPException(status_code=404, detail="Camera not found")

    # Here you would typically use a library specific to Hikvision cameras
    # This is a simplified example using requests
    url = f"http://{camera_ip}/capture"

    try:
        response = requests.get(url)
        response.raise_for_status()
        logging.info(f"Photo captured successfully from IP: {camera_ip}")
        return {"photo": response.content}
    except requests.RequestException as e:
        logging.error(f"Error capturing photo from IP {camera_ip}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    logging.info("Starting FastAPI application")
    uvicorn.run(app, host="0.0.0.0", port=8000)
