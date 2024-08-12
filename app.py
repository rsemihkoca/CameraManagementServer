import cv2
import logging
import os
import requests
import time
from fastapi import FastAPI, Response, HTTPException
from requests.auth import HTTPDigestAuth, HTTPBasicAuth
from threading import Thread
from dotenv import load_dotenv
from starlette.responses import StreamingResponse

load_dotenv()

ip = os.environ.get("IP_CAMERA_ADDRESS")
http_port = os.environ.get("IP_CAMERA_HTTP_PORT")
rtsp_port = os.environ.get("IP_CAMERA_RTSP_PORT")
CAMERA_USERNAME = os.environ.get("IP_CAMERA_USERNAME")
CAMERA_PASSWORD = os.environ.get("IP_CAMERA_PASSWORD")
port = os.environ.get("IP_CAMERA_CONTAINER_PORT")

CAMERA_CTRL_MOVE_UP = '<?xml version="1.0" encoding="UTF-8"?><PTZData><pan>0</pan><tilt>60</tilt></PTZData>'
CAMERA_CTRL_MOVE_DOWN = '<?xml version="1.0" encoding="UTF-8"?><PTZData><pan>0</pan><tilt>-60</tilt></PTZData>'
CAMERA_CTRL_MOVE_LEFT = '<?xml version="1.0" encoding="UTF-8"?><PTZData><pan>-60</pan><tilt>0</tilt></PTZData>'
CAMERA_CTRL_MOVE_RIGHT = '<?xml version="1.0" encoding="UTF-8"?><PTZData><pan>60</pan><tilt>0</tilt></PTZData>'
CAMERA_CTRL_MOVE_STOP = '<?xml version="1.0" encoding="UTF-8"?><PTZData><pan>0</pan><tilt>0</tilt></PTZData>'

CAMERA_CTRL_MOVE_DICT = {
    "up": CAMERA_CTRL_MOVE_UP,
    "down": CAMERA_CTRL_MOVE_DOWN,
    "left": CAMERA_CTRL_MOVE_LEFT,
    "right": CAMERA_CTRL_MOVE_RIGHT,
}

app = FastAPI()

class VideoGet:
    """
    Class that continuously gets frames from a VideoCapture object
    with a dedicated thread.
    """

    def __init__(self, ip, rtsp_port, username, password):
        self.stream = cv2.VideoCapture(f"rtsp://{username}:{password}@{ip}{rtsp_port}")
        (self.grabbed, self.frame) = self.stream.read()
        self.stopped = False

    def start(self):
        self.thread = Thread(target=self.get, args=())
        self.thread.start()
        return self

    def get(self):
        while not self.stopped:
            if not self.grabbed:
                print("Capture failed, please refresh the page!")
                break
            else:
                (self.grabbed, self.frame) = self.stream.read()

    def stop(self):
        self.stopped = True
        self.thread.join()

video_getter = VideoGet(ip, rtsp_port, CAMERA_USERNAME, CAMERA_PASSWORD).start()

@app.get("/")
async def home():
    return Response(content="Welcome to IP Camera API\n", media_type='text/plain')

@app.get("/capture")
async def capture():
    try:
        global video_getter
        if not video_getter.grabbed:
            video_getter.stop()
            video_getter = VideoGet(ip, rtsp_port, CAMERA_USERNAME, CAMERA_PASSWORD).start()
        if not video_getter.stopped:
            ret, frame = video_getter.grabbed, video_getter.frame
            if ret:
                retval, buffer = cv2.imencode('.jpg', frame)
                byte_frame = buffer.tobytes()
                print("Image captured!")
                return Response(content=byte_frame, media_type='image/jpeg')
            else:
                print("Cannot capture frame from cv2")
                raise HTTPException(status_code=400, detail="Cannot capture frame from cv2")
    except Exception as e:
        print(f"Error capture picture, error: {e}")
        raise HTTPException(status_code=400, detail="Cannot capture frame from cv2")

def stream():
    try:
        print("Start streaming!")
        while True:
            success, frame = video_getter.grabbed, video_getter.frame
            if not success:
                break
            else:
                reduced_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                ret, buffer = cv2.imencode('.jpeg', reduced_frame)
                frame_data = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')  # Concat frame one by one and show result
    except Exception as e:
        print(f"Error capture picture, error: {e}")
        return False

def getCameraInfoWithAuth(s, ip, http_port, auth):
    result = None
    s.auth = auth
    try:
        # Add digest authentication
        r = s.get(f'http://{ip}{http_port}/PSIA/System/deviceInfo', auth=HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD))
        if r.ok:
            result = r.content
        else:
            r = s.get(f'http://{ip}{http_port}/ISAPI/System/deviceInfo', auth=HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD))
            if r.ok:
                result = r.content
            else:
                print(f"{type(auth)} failed")
    except Exception as e:
        result = None
        print(f"Error trying {type(auth)}, {e}")

    return result

def moveCameraWithAuth(s, ip, http_port, auth, direction):
    result = None
    s.auth = auth
    try:
        # Send request once to avoid send put request error
        getCameraInfoWithAuth(s, ip, http_port, auth)
        headers = {'Content-Type': 'application/xml'}
        r = s.put(f'http://{ip}{http_port}/ISAPI/PTZCtrl/channels/1/continuous',
                  data=CAMERA_CTRL_MOVE_DICT[direction], headers=headers)
        if r.ok:
            time.sleep(0.2)
            r = s.put(f'http://{ip}{http_port}/ISAPI/PTZCtrl/channels/1/continuous', data=CAMERA_CTRL_MOVE_STOP,
                      headers=headers)
            result = r.content
        else:
            print(f"{type(auth)} failed, message: {r.content}")
    except Exception as e:
        result = None
        print(f"Error trying {type(auth)}, {e}")

    return result

def moveCamera(direction):
    with requests.Session() as s:
        result = None
        print("Try HTTPDigestAuth")
        auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
        result = moveCameraWithAuth(s, ip, http_port, auth, direction)

        if result is None:
            print("Try HTTPBasicAuth")
            auth = HTTPBasicAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
            result = moveCameraWithAuth(s, ip, http_port, auth, direction)
            if result is None:
                print("All authentication failed for device")
                return False

        return True

@app.get("/info")
async def getCameraInfo():
    with requests.Session() as s:
        result = None
        print("Try HTTPDigestAuth")
        auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
        result = getCameraInfoWithAuth(s, ip, http_port, auth)

        if result is None:
            print("Try HTTPBasicAuth")
            auth = HTTPBasicAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
            result = getCameraInfoWithAuth(s, ip, http_port, auth)

        if result is None:
            print("All authentication failed for device")
            raise HTTPException(status_code=400, detail="All authentication failed for device")

        return Response(content=result, media_type='text/xml')

@app.get("/stream")
async def video_feed():
    global video_getter
    if not video_getter.grabbed:
        video_getter.stop()
        video_getter = VideoGet(ip, rtsp_port, CAMERA_USERNAME, CAMERA_PASSWORD).start()
    return StreamingResponse(stream(), media_type='multipart/x-mixed-replace; boundary=frame')

@app.get("/move/{direction}")
async def move_camera(direction: str):
    print(CAMERA_CTRL_MOVE_DICT.keys())
    print(f"Direction is {direction}")
    if direction is None or direction not in CAMERA_CTRL_MOVE_DICT.keys():
        raise HTTPException(status_code=400, detail="Please specify move direction, /move/(up/down/left/right)")
    if moveCamera(direction):
        return {"status": "Success"}
    else:
        raise HTTPException(status_code=400, detail="Cannot move camera")


# @app.get("/stream/{camera_ip}")
# async def stream_video(camera_ip: str):
#     db = load_db()
#     camera = next((c for c in db if c["ip"] == camera_ip), None)
#
#     if not camera:
#         raise HTTPException(status_code=404, detail="Camera not found in the database")
#
#     # Test connection
#     try:
#         test_connection(CameraConnection(ip=camera_ip))
#     except HTTPException as e:
#         raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e.detail)}")
#
#     # RTSP URL for Hikvision cameras
#     rtsp_url = f"rtsp://{CAMERA_USERNAME}:{CAMERA_PASSWORD}@{camera_ip}:554/Streaming/Channels/101"
#
#     async def generate():
#         container = av.open(rtsp_url, options={'rtsp_transport': 'tcp'})
#         for frame in container.decode(video=0):
#             yield (b'--frame\r\n'
#                    b'Content-Type: image/jpeg\r\n\r\n' + frame.to_image().tobytes() + b'\r\n')
#             await asyncio.sleep(0.1)  # Adjust this value to control frame rate
#
#     return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


# @app.get("/stream/{camera_ip}")
# async def stream_video(camera_ip: str):
#     db = load_db()
#     camera = next((c for c in db if c["ip"] == camera_ip), None)
#
#     if not camera:
#         raise HTTPException(status_code=404, detail="Camera not found in the database")
#
#     # Test connection
#     try:
#         test_connection(CameraConnection(ip=camera_ip))
#     except HTTPException as e:
#         raise HTTPException(status_code=400, detail=f"Connection test failed: {str(e.detail)}")
#
#     # Stream video
#     url = f"http://{camera_ip}/ISAPI/Streaming/channels/1/httpPreview"
#     auth = HTTPDigestAuth(CAMERA_USERNAME, CAMERA_PASSWORD)
#
#     def stream():
#         try:
#             with requests.get(url, auth=auth, stream=True, timeout=10) as r:
#                 r.raise_for_status()
#                 for chunk in r.iter_content(chunk_size=8192):
#                     yield chunk
#         except requests.RequestException as e:
#             raise HTTPException(status_code=500, detail=f"Failed to stream video: {str(e)}")
#
#     return StreamingResponse(stream(), media_type="video/mp4")


if __name__ == "__main__":
    import uvicorn
    # Customize port
    if http_port:
        http_port = ':' + http_port

    if rtsp_port:
        rtsp_port = ':' + rtsp_port

    video_getter = VideoGet(ip, rtsp_port, CAMERA_USERNAME, CAMERA_PASSWORD).start()
    uvicorn.run(app, host="0.0.0.0", port=int(port))
