import json
import boto3
import os

REGION = "ap-south-1"
JOB_TABLE = "LensJobStatus"
COLLECTION_ID = "OrgFaces"
SNS_TOPIC_ARN = "arn:aws:sns:ap-south-1:760402325768:LensRekognitionComplete"
REK_ROLE_ARN = "arn:aws:iam::760402325768:role/LensRekognitionSNSRole"

rekognition = boto3.client("rekognition", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)

def lambda_handler(event, context):
    for record in event["Records"]:
        body = json.loads(record["body"])

        video_id = body["video_id"]
        bucket   = body["bucket"]
        video_key = body["video_key"]

        # Start async face search, tell Rekognition to ping SNS when done
        response = rekognition.start_face_search(
            Video={
                "S3Object": {
                    "Bucket": bucket,
                    "Name": video_key
                }
            },
            CollectionId=COLLECTION_ID,
            FaceMatchThreshold=80.0,
            NotificationChannel={
                "SNSTopicArn": SNS_TOPIC_ARN,
                "RoleArn": REK_ROLE_ARN
            }
        )

        job_id = response["JobId"]

        # Save status to DynamoDB
        table = dynamodb.Table(JOB_TABLE)
        table.put_item(Item={
            "video_id": video_id,
            "job_id": job_id,
            "status": "PROCESSING",
            "bucket": bucket,
            "video_key": video_key,
            "faces_to_search": body.get("faces_to_search", [])
        })

        print(f"Started job {job_id} for {video_id}")

    return {"statusCode": 200}