import json
import boto3
import os
import cv2
import numpy as np
from decimal import Decimal

REGION               = "ap-south-1"
JOB_TABLE            = "LensJobStatus"
RESULTS_TABLE        = "LensResults"
SIMILARITY_THRESHOLD = 80.0  # % — raise to reduce false positives, lower to catch more

rekognition = boto3.client("rekognition", region_name=REGION)
dynamodb    = boto3.resource("dynamodb",   region_name=REGION)
s3          = boto3.client("s3",           region_name=REGION)

def ms_to_timestamp(ms):
    seconds = int(ms / 1000)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02}:{m:02}:{s:02}"

def aggregate_segments(detections, gap_ms=3000):
    if not detections:
        return []
    detections = sorted(detections, key=lambda x: x["timestamp"])
    segments = []
    current = [detections[0]]
    for det in detections[1:]:
        if det["timestamp"] - current[-1]["timestamp"] <= gap_ms:
            current.append(det)
        else:
            segments.append(current)
            current = [det]
    segments.append(current)

    result = []
    for seg in segments:
        best = max(seg, key=lambda x: x["confidence"])
        result.append({
            "start_time":   ms_to_timestamp(seg[0]["timestamp"]),
            "end_time":     ms_to_timestamp(seg[-1]["timestamp"]),
            "confidence":   best["confidence"],
            "best_ms":      best["timestamp"],
            "bounding_box": best["bounding_box"]
        })
    return result

def generate_screenshot(bucket, video_key, best_ms, bounding_box, person, video_id):
    local_video = "/tmp/video.mp4"
    local_screenshot = "/tmp/screenshot.jpg"

    try:
        s3.download_file(bucket, video_key, local_video)

        cap = cv2.VideoCapture(local_video)
        cap.set(cv2.CAP_PROP_POS_MSEC, best_ms)
        success, frame = cap.read()
        cap.release()

        if not success:
            print(f"Failed to extract frame at {best_ms}ms")
            return None

        h, w = frame.shape[:2]
        left   = int(bounding_box.get("Left", 0) * w)
        top    = int(bounding_box.get("Top", 0) * h)
        width  = int(bounding_box.get("Width", 0) * w)
        height = int(bounding_box.get("Height", 0) * h)

        cv2.rectangle(frame, (left, top), (left + width, top + height), (0, 255, 0), 2)
        cv2.putText(frame, person, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.imwrite(local_screenshot, frame)

        screenshot_key = f"screenshots/{person}_{video_id}_{best_ms}.jpg"
        s3.upload_file(local_screenshot, bucket, screenshot_key)

        return f"s3://{bucket}/{screenshot_key}"

    finally:
        if os.path.exists(local_video):
            os.remove(local_video)
        if os.path.exists(local_screenshot):
            os.remove(local_screenshot)

def lambda_handler(event, context):
    sns_message = json.loads(event["Records"][0]["Sns"]["Message"])
    job_id      = sns_message["JobId"]
    status      = sns_message["Status"]
    video_key   = sns_message["Video"]["S3ObjectName"]
    video_id    = os.path.basename(video_key)

    if status != "SUCCEEDED":
        print(f"Job {job_id} failed with status {status}")
        dynamodb.Table(JOB_TABLE).update_item(
            Key={"video_id": video_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "FAILED"}
        )
        return {"statusCode": 200}

    job    = dynamodb.Table(JOB_TABLE).get_item(Key={"video_id": video_id}).get("Item", {})
    bucket = job.get("bucket")
    allowed_faces = [f.lower() for f in job.get("faces_to_search", [])]

    persons    = []
    next_token = None
    while True:
        kwargs = {"JobId": job_id, "MaxResults": 1000, "SortBy": "TIMESTAMP"}
        if next_token:
            kwargs["NextToken"] = next_token
        res = rekognition.get_face_search(**kwargs)
        persons.extend(res.get("Persons", []))
        next_token = res.get("NextToken")
        if not next_token:
            break

    by_person = {}
    for entry in persons:
        matches = entry.get("FaceMatches", [])
        if not matches:
            continue
        best_match  = matches[0]
        similarity  = best_match["Similarity"]

        # Skip low-confidence matches to avoid false positives
        if similarity < SIMILARITY_THRESHOLD:
            continue

        person_name = best_match["Face"].get("ExternalImageId", "")

        if allowed_faces and person_name.lower() not in allowed_faces:
            continue

        # Use the face bounding box (always present on a match).
        # Fallback to person bounding box only if face box is missing.
        face_bb   = entry["Person"].get("Face", {}).get("BoundingBox", {})
        person_bb = entry["Person"].get("BoundingBox", {})
        bounding_box = face_bb if face_bb else person_bb

        if person_name not in by_person:
            by_person[person_name] = []
        by_person[person_name].append({
            "timestamp":    entry["Timestamp"],
            "confidence":   similarity,
            "bounding_box": bounding_box
        })

    results_table = dynamodb.Table(RESULTS_TABLE)

    for person, dets in by_person.items():
        segments = aggregate_segments(dets)
        for seg in segments:
            bb = seg["bounding_box"]

            screenshot_url = generate_screenshot(
                bucket, video_key, seg["best_ms"], bb, person, video_id
            )

            results_table.put_item(Item={
                "person":          person,
                "video_timestamp": f"{video_id}#{seg['start_time']}",
                "end_time":        seg["end_time"],
                "confidence":      Decimal(str(round(seg["confidence"], 2))),
                "screenshot_url":  screenshot_url or "FAILED",
                "bounding_box": {
                    "Left":   Decimal(str(bb.get("Left",   0))),
                    "Top":    Decimal(str(bb.get("Top",    0))),
                    "Width":  Decimal(str(bb.get("Width",  0))),
                    "Height": Decimal(str(bb.get("Height", 0)))
                },
                "best_ms":   seg["best_ms"],
                "video_key": video_key,
                "bucket":    bucket
            })
            print(f"Saved: {person} in {video_id} at {seg['start_time']} → {screenshot_url}")

    dynamodb.Table(JOB_TABLE).update_item(
        Key={"video_id": video_id},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "COMPLETE"}
    )

    print(f"Job {job_id} complete.")
    return {"statusCode": 200}