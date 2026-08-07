# FaceTrace — Video Face Detection

> Upload a video. Enroll faces. Get back timestamps and screenshots of every moment a known person appears powered by AWS Rekognition.

---

## What it does

FaceTrace lets you find specific people in video footage at scale. You enroll someone with one or more photos, upload a video, and AWS processes it asynchronously in the cloud. When done, you get:

- **Timestamps** of every frame where the person was detected
- **Confidence scores** for each match (≥80% threshold)
- **Screenshot thumbnails** with bounding boxes around detected faces
- A **dashboard** to browse results by person and video

Built with **Streamlit** (UI) and fully serverless **AWS** infrastructure. Your machine only runs the browser UI — all heavy lifting happens in the cloud.

---

## Architecture

```
[Face Enrollment — synchronous, instant]
  app.py → Rekognition IndexFaces → OrgFaces collection
                                    (stores face embeddings)

[Video Processing — asynchronous, ~1-5 min]
  app.py → S3 (upload video)
         → SQS (LensJobQueue)          ← fire and forget
              ↓
         P2_Starter Lambda             ← picks up job from queue
              ↓
         Rekognition StartFaceSearch   ← async face matching
              ↓
         SNS (LensRekognitionComplete) ← "job done" notification
              ↓
         P2_Fetcher Lambda             ← fetches results, saves frames
              ↓
         DynamoDB (LensResults)        ← app reads results from here
```

**AWS resources used:**

| Resource | Name | Purpose |
|---|---|---|
| S3 Bucket | `video-detection-system` | Store videos, face images, result screenshots |
| SQS Queue | `LensJobQueue` | Buffer video processing jobs |
| SNS Topic | `LensRekognitionComplete` | Notify when Rekognition finishes |
| Lambda | `P2_Starter` | Trigger Rekognition face search |
| Lambda | `P2_Fetcher` | Collect results, extract frames, save to DynamoDB |
| DynamoDB | `LensJobStatus` | Track job progress per video |
| DynamoDB | `LensResults` | Store per-frame match results |
| Rekognition | `OrgFaces` | Face collection (enrolled people) |
| IAM Role | `P2-Starter-LambdaRole` | Permissions for Starter Lambda |
| IAM Role | `P2-Fetcher-LambdaRole` | Permissions for Fetcher Lambda |
| IAM Role | `LensRekognitionSNSRole` | Allows Rekognition to publish to SNS |

---

## Quick Start

### Prerequisites

- Python 3.10+
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- An AWS account configured with `aws configure` (region: `ap-south-1`)

### Setup & Run

```powershell
# 1. Configure AWS (one-time)
aws configure
# Enter: Access Key ID, Secret Access Key, region = ap-south-1, output = json

# 2. Provision all AWS resources + install Python deps
.\setup.ps1

# 3. Launch the app
.\setup.ps1 -Run
# → Open http://localhost:8501
```

Re-running `.\setup.ps1` is **safe and idempotent** — it checks what already exists, skips it, and only creates what's missing.

> ⚠️ **Fixed resource names warning**
>
> This project uses hardcoded resource names (`video-detection-system`, `LensJobQueue`, `OrgFaces`, etc.) in region `ap-south-1`.
> Do **not** run setup in an AWS account where another app already uses these names.
> Use a dedicated AWS account.

---

## All Commands

| Command | What it does |
|---|---|
| `.\setup.ps1` | Create everything missing (venv, AWS infra, Lambdas, triggers) |
| `.\setup.ps1 -SkipAws` | Python `.venv` only — no AWS changes |
| `.\setup.ps1 -RedeployLambdas` | Force re-upload Lambda code (use after editing `lambdas/`) |
| `.\setup.ps1 -Check` | Health check — verify all AWS resources and local env |
| `.\setup.ps1 -Run` | Start the Streamlit app |
| `.\setup.ps1 -Destroy` | Delete all AWS resources (Lambdas, S3, DynamoDB, SQS, SNS, Rekognition) |

---

## Using the App

### 1. Enroll Faces (Face Library)

- Go to the **Face Library** tab
- Upload a photo and enter the person's name (e.g. `kshitij`)
- One clear photo per person is enough; multiple angles improve accuracy
- Only one face per photo is indexed (the most prominent one)
- Use the 🗑 button to remove an enrolled person from the collection

### 2. Upload a Video

- Go to **Video Upload**
- Upload an MP4/MOV file — it gets sent to S3 and queued for processing
- Status auto-refreshes: `queued → processing → complete`
- Processing takes 1–5 minutes depending on video length

### 3. View Results

- Go to the **Results Dashboard**
- Select identities and videos from the sidebar
- See timestamps, confidence scores, and screenshot frames with bounding boxes

---

## Project Structure

```
app.py               Streamlit UI (all pages)
lambdas/
  P2_Starter.py      Lambda: reads SQS, triggers Rekognition StartFaceSearch
  P2_Fetcher.py      Lambda: receives SNS, fetches results, saves frames to DynamoDB
setup.ps1            One-script setup: Python env + full AWS provisioning + health check
requirements.txt     Python dependencies (boto3, streamlit, opencv)
```

---

## IAM Permissions

### Root user

No extra setup needed — root has full access. Just `aws configure` and run `.\setup.ps1`.

> Avoid using root for day-to-day work. Create a dedicated IAM user for regular use.

### IAM user — running setup (`.\setup.ps1`)

Attach these **managed policies** to your IAM user:

| Policy | Purpose |
|---|---|
| `AWSLambda_FullAccess` | Create/update Lambdas, wire SQS & SNS triggers |
| `AmazonS3FullAccess` | Create bucket, upload/download objects |
| `AmazonDynamoDBFullAccess` | Create and manage tables |
| `AmazonSQSFullAccess` | Create queue |
| `AmazonSNSFullAccess` | Create topic, subscribe Fetcher Lambda |
| `AmazonRekognitionFullAccess` | Create face collection |
| `CloudWatchLogsFullAccess` | Lambda log groups |

Also attach this **custom inline policy** (instead of `IAMFullAccess`) to allow setup to create Lambda execution roles:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "FaceTraceSetupRoles",
      "Effect": "Allow",
      "Action": [
        "iam:GetRole",
        "iam:CreateRole",
        "iam:PutRolePolicy"
      ],
      "Resource": [
        "arn:aws:iam::*:role/LensRekognitionSNSRole",
        "arn:aws:iam::*:role/P2-Starter-LambdaRole",
        "arn:aws:iam::*:role/P2-Fetcher-LambdaRole"
      ]
    },
    {
      "Sid": "FaceTracePassRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::*:role/LensRekognitionSNSRole",
        "arn:aws:iam::*:role/P2-Starter-LambdaRole",
        "arn:aws:iam::*:role/P2-Fetcher-LambdaRole"
      ]
    }
  ]
}
```

Replace `*` with your 12-digit AWS account ID if your org requires scoped resources.

### IAM user — app only (setup already done)

If Lambdas and roles already exist, you only need:

| Policy | Purpose |
|---|---|
| `AmazonS3FullAccess` | Upload faces/videos, read screenshots |
| `AmazonSQSFullAccess` | Queue videos for processing |
| `AmazonDynamoDBFullAccess` | Read job status and results |
| `AmazonRekognitionFullAccess` | Enroll faces, list collection |

---

## Troubleshooting

### Health check

```powershell
.\setup.ps1 -Check
```

---

### Video stuck on "processing"

Check Lambda logs:

```powershell
aws logs tail /aws/lambda/P2_Starter --region ap-south-1 --since 30m
aws logs tail /aws/lambda/P2_Fetcher --region ap-south-1 --since 30m
```

**Common causes:**

| Symptom | Cause | Fix |
|---|---|---|
| `P2_Starter` logs show `InvalidS3ObjectException` | Starter role missing `s3:GetObject` | Re-run `.\setup.ps1` |
| `P2_Starter` runs but `P2_Fetcher` never triggers | Lambda resource policy missing — SNS can't invoke Fetcher | Re-run `.\setup.ps1` |
| `P2_Fetcher` logs show permission errors on DynamoDB | Fetcher role missing table access | Re-run `.\setup.ps1` |
| Video stays queued, `P2_Starter` never runs | SQS-to-Lambda mapping missing or disabled | Re-run `.\setup.ps1` |

---

### Re-deploy Lambda code after changes

```powershell
.\setup.ps1 -RedeployLambdas
```

### Full reset

```powershell
.\setup.ps1 -Destroy   # delete all AWS resources
.\setup.ps1            # recreate everything fresh
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| UI | [Streamlit](https://streamlit.io/) |
| Face recognition | [AWS Rekognition](https://aws.amazon.com/rekognition/) |
| Storage | AWS S3, DynamoDB |
| Async processing | AWS SQS, SNS, Lambda |
| Language | Python 3.10+ |
| Infrastructure | AWS CLI + PowerShell setup script |
