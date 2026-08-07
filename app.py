import streamlit as st
import boto3
import json
import time
import os
from boto3.dynamodb.conditions import Key, Attr
from boto3.s3.transfer import TransferConfig
from dotenv import load_dotenv

# Load local environment variables from .env if present
load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
REGION        = os.getenv("AWS_REGION", "ap-south-1")
BUCKET        = "video-detection-system"
COLLECTION_ID = "OrgFaces"
JOB_TABLE     = "LensJobStatus"
RESULTS_TABLE = "LensResults"

# ── Resolve Account ID and SQS URL Dynamically ──────────────────────────────
try:
    # Use STS to fetch current user's AWS Account ID automatically
    sts_client = boto3.client("sts", region_name=REGION)
    ACCOUNT_ID = sts_client.get_caller_identity()["Account"]
except Exception:
    # Fallback to env variable or your original hardcoded ID
    ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID", "760402325768")

QUEUE_URL = f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/LensJobQueue"

# ── Clients ──────────────────────────────────────────────────────────────────
s3          = boto3.client("s3",          region_name=REGION)
sqs         = boto3.client("sqs",         region_name=REGION)
rekognition = boto3.client("rekognition", region_name=REGION)
dynamodb    = boto3.resource("dynamodb",  region_name=REGION)

VIDEO_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold = 10 * 1024 * 1024,
    multipart_chunksize = 10 * 1024 * 1024,
    max_concurrency     = 1,
    use_threads         = False
)

# ── CSS ───────────────────────────────────────────────────────────────────────
def inject_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif !important; }
    .stApp { background-color: #f7f6f3 !important; }
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding: 2rem 2.5rem !important; max-width: 1200px; }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #ffffff !important;
        border-right: 1px solid #e5e7eb !important;
    }
    [data-testid="stSidebar"] .stRadio label {
        font-size: 0.875rem !important;
        color: #374151 !important;
        padding: 0.3rem 0 !important;
    }
    [data-testid="stSidebar"] .stCheckbox label {
        font-size: 0.82rem !important;
        color: #374151 !important;
    }
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
        font-size: 0.72rem !important;
        color: #9ca3af !important;
        text-transform: uppercase !important;
        letter-spacing: 0.08em !important;
    }

    /* Buttons */
    .stButton > button {
        background: #dc2626 !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 6px !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        padding: 0.5rem 1.5rem !important;
        transition: background 0.15s !important;
    }
    .stButton > button:hover    { background: #b91c1c !important; }
    .stButton > button:disabled { background: #fca5a5 !important; }

    /* Inputs */
    .stTextInput input {
        background: #ffffff !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 6px !important;
        color: #111827 !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 0.875rem !important;
    }
    .stTextInput input:focus {
        border-color: #dc2626 !important;
        box-shadow: 0 0 0 3px #fee2e2 !important;
    }

    /* Progress */
    .stProgress > div > div { background-color: #dc2626 !important; }

    /* Divider */
    hr { border-color: #e5e7eb !important; margin: 0.75rem 0 !important; }

    /* File uploader */
    [data-testid="stFileUploader"] {
        background: #ffffff !important;
        border: 1.5px dashed #e5e7eb !important;
        border-radius: 8px !important;
    }

    /* Alerts */
    .stSuccess { background: #f0fdf4 !important; border: 1px solid #bbf7d0 !important; }
    .stError   { background: #fef2f2 !important; border: 1px solid #fecaca !important; }
    .stInfo    { background: #eff6ff !important; border: 1px solid #bfdbfe !important; }
    .stWarning { background: #fffbeb !important; border: 1px solid #fde68a !important; }

    /* Multiselect */
    [data-testid="stMultiSelect"] { background: #ffffff !important; }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: #f7f6f3; }
    ::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 2px; }
    ::-webkit-scrollbar-thumb:hover { background: #dc2626; }

    /* ── Custom component classes ── */
    .lens-title {
        font-size: 1.4rem;
        font-weight: 600;
        color: #111827;
        margin-bottom: 0.2rem;
    }
    .lens-subtitle {
        font-size: 0.875rem;
        color: #6b7280;
        margin-bottom: 1.75rem;
    }

    .stat-card {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        text-align: center;
    }
    .stat-value {
        font-size: 1.4rem;
        font-weight: 600;
        color: #dc2626;
    }
    .stat-label {
        font-size: 0.65rem;
        color: #9ca3af;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-top: 0.2rem;
    }

    .result-card {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        transition: border-color 0.15s;
    }
    .result-card:hover { border-color: #fca5a5; }

    .not-found-card {
        background: #fef2f2;
        border: 1px solid #fecaca;
        border-left: 3px solid #dc2626;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        margin-bottom: 0.5rem;
        font-size: 0.8rem;
        color: #dc2626;
    }

    .video-section-header {
        font-size: 0.7rem;
        color: #9ca3af;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin: 1.5rem 0 0.75rem 0;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid #e5e7eb;
    }

    .person-header {
        font-size: 0.9rem;
        font-weight: 600;
        color: #111827;
        letter-spacing: 0.02em;
        margin: 2rem 0 0.5rem 0;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    .timestamp-badge {
        display: inline-block;
        background: #eff6ff;
        border: 1px solid #bfdbfe;
        color: #1d4ed8;
        font-size: 0.78rem;
        padding: 0.2rem 0.6rem;
        border-radius: 4px;
        margin-right: 0.3rem;
    }

    .status-pill {
        display: inline-block;
        font-size: 0.68rem;
        padding: 0.15rem 0.6rem;
        border-radius: 20px;
        font-weight: 500;
    }
    .pill-processing { background: #eff6ff; color: #1d4ed8; }
    .pill-complete   { background: #f0fdf4; color: #166534; }
    .pill-failed     { background: #fef2f2; color: #dc2626; }
    .pill-pending    { background: #fffbeb; color: #d97706; }
    </style>
    """, unsafe_allow_html=True)

# ── Helpers ──────────────────────────────────────────────────────────────────
def get_enrolled_people():
    names  = set()
    kwargs = {"CollectionId": COLLECTION_ID, "MaxResults": 100}
    while True:
        resp = rekognition.list_faces(**kwargs)
        for face in resp.get("Faces", []):
            ext_id = face.get("ExternalImageId", "")
            if ext_id:
                names.add(ext_id)
        token = resp.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    return sorted(names)

def delete_person(name):
    """Remove all faces for a given ExternalImageId from the Rekognition collection."""
    face_ids = []
    kwargs   = {"CollectionId": COLLECTION_ID, "MaxResults": 100}
    while True:
        resp = rekognition.list_faces(**kwargs)
        for face in resp.get("Faces", []):
            if face.get("ExternalImageId", "") == name:
                face_ids.append(face["FaceId"])
        token = resp.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token

    # Rekognition delete_faces accepts max 4096 IDs at a time
    for i in range(0, len(face_ids), 4096):
        rekognition.delete_faces(
            CollectionId=COLLECTION_ID,
            FaceIds=face_ids[i:i+4096]
        )

    # Also delete the face images from S3
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=f"faces/{name}/"):
            for obj in page.get("Contents", []):
                s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
    except Exception:
        pass  # S3 cleanup is best-effort

def get_s3_videos():
    videos = []
    kwargs = {"Bucket": BUCKET, "Prefix": "videos/"}
    while True:
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key != "videos/":
                videos.append(key.replace("videos/", ""))
        token = resp.get("NextContinuationToken")
        if not token:
            break
        kwargs["ContinuationToken"] = token
    return sorted(videos)

def get_presigned_url(screenshot_url, expiry=3600):
    if not screenshot_url or screenshot_url in ("FAILED", "PENDING"):
        return None
    try:
        path        = screenshot_url.replace("s3://", "")
        bucket, key = path.split("/", 1)
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry
        )
    except Exception:
        return None

def get_job_status(video_id):
    resp = dynamodb.Table(JOB_TABLE).get_item(Key={"video_id": video_id})
    return resp.get("Item", {}).get("status", "UNKNOWN")

def get_results(person_name):
    resp = dynamodb.Table(RESULTS_TABLE).query(
        KeyConditionExpression=Key("person").eq(person_name)
    )
    return resp.get("Items", [])

def delete_video(video_id):
    # 1. Delete video from S3
    s3.delete_object(Bucket=BUCKET, Key=f"videos/{video_id}")

    # 2. Delete screenshots from S3
    kwargs = {"Bucket": BUCKET, "Prefix": "screenshots/"}
    while True:
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if f"_{video_id}_" in obj["Key"]:
                s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
        token = resp.get("NextContinuationToken")
        if not token:
            break
        kwargs["ContinuationToken"] = token

    # 3. Delete results from DynamoDB LensResults
    table = dynamodb.Table(RESULTS_TABLE)
    resp  = table.scan(
        FilterExpression=Attr("video_timestamp").begins_with(video_id + "#")
    )
    for item in resp.get("Items", []):
        table.delete_item(Key={
            "person":          item["person"],
            "video_timestamp": item["video_timestamp"]
        })

    # 4. Delete job record from LensJobStatus
    dynamodb.Table(JOB_TABLE).delete_item(Key={"video_id": video_id})

# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown("""
        <div style="padding:0.25rem 0 0 0;">
            <div style="font-size:1.15rem;font-weight:700;color:#dc2626;
                        letter-spacing:0.05em;margin-bottom:0.1rem;">FaceTrace</div>
            <div style="font-size:0.65rem;color:#9ca3af;margin-bottom:1.75rem;">
                v1.0 · ap-south-1
            </div>
        </div>""", unsafe_allow_html=True)

        page = st.radio(
    "Navigation",
    ["Face Library", "Video Upload", "Results Dashboard"],
    label_visibility="collapsed"
)
        return page

# ── Page: Face Library ────────────────────────────────────────────────────────
def page_face_library():
    st.markdown('<div class="lens-title">Face Enrollment</div>', unsafe_allow_html=True)
    st.markdown('<div class="lens-subtitle">Enroll identities into the recognition collection</div>', unsafe_allow_html=True)

    people = get_enrolled_people()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{len(people)}</div><div class="stat-label">Enrolled</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{COLLECTION_ID}</div><div class="stat-label">Collection</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="stat-card"><div class="stat-value">80%</div><div class="stat-label">Match threshold</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown('<div class="video-section-header">Enroll new identity</div>', unsafe_allow_html=True)
        name = st.text_input("Name", placeholder="e.g. john", label_visibility="collapsed").strip().lower()
        st.caption("Identity name (lowercase, no spaces)")
        images = st.file_uploader(
            "Face images",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            label_visibility="collapsed"
        )
        st.caption("Upload multiple front-facing images for better accuracy")

        if st.button("Enroll identity", disabled=not (name and images)):
            with st.spinner("Enrolling..."):
                failed = []
                for img in images:
                    try:
                        s3_key = f"faces/{name}/{img.name}"
                        s3.upload_fileobj(img, BUCKET, s3_key)
                        result = rekognition.index_faces(
                            CollectionId=COLLECTION_ID,
                            Image={"S3Object": {"Bucket": BUCKET, "Name": s3_key}},
                            ExternalImageId=name,
                            MaxFaces=1,
                            DetectionAttributes=[],
                            QualityFilter="AUTO"
                        )
                        if not result.get("FaceRecords"):
                            failed.append(img.name)
                    except Exception:
                        failed.append(img.name)

            enrolled = len(images) - len(failed)
            if enrolled:
                st.success(f"Enrolled **{name}** — {enrolled} image(s) indexed.")
            if failed:
                st.warning(f"No face detected: {', '.join(failed)}")

    with col_right:
        st.markdown('<div class="video-section-header">Enrolled identities</div>', unsafe_allow_html=True)
        if not people:
            st.markdown("""
            <div class="not-found-card">No identities enrolled yet</div>
            """, unsafe_allow_html=True)
        else:
            for person in people:
                initial = person[0].upper() if person else "?"
                col_card, col_del = st.columns([11, 1])
                with col_card:
                    st.markdown(f"""
                    <div class="result-card" style="padding:0.6rem 1rem;display:flex;align-items:center;gap:0.75rem;margin-bottom:0;">
                        <div style="width:30px;height:30px;background:#fee2e2;border-radius:50%;
                                    display:flex;align-items:center;justify-content:center;
                                    font-size:0.75rem;font-weight:600;color:#dc2626;flex-shrink:0;">
                            {initial}
                        </div>
                        <span style="font-size:0.875rem;color:#111827;font-weight:500;">{person}</span>
                    </div>""", unsafe_allow_html=True)
                with col_del:
                    if st.button("🗑", key=f"del_person_{person}"):
                        st.session_state[f"confirm_person_{person}"] = True
                        st.rerun()

                if st.session_state.get(f"confirm_person_{person}"):
                    st.markdown(f"""
                    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;
                                padding:0.6rem 1rem;font-size:0.85rem;color:#dc2626;margin:0.25rem 0 0.5rem 0;">
                        Remove <strong>{person}</strong> from the collection?
                    </div>""", unsafe_allow_html=True)
                    c1, c2, _ = st.columns([1, 1, 6])
                    with c1:
                        if st.button("Delete", key=f"confirm_yes_person_{person}"):
                            with st.spinner(f"Removing {person}..."):
                                delete_person(person)
                            if f"confirm_person_{person}" in st.session_state:
                                del st.session_state[f"confirm_person_{person}"]
                            st.success(f"Removed {person} from collection.")
                            time.sleep(1)
                            st.rerun()
                    with c2:
                        if st.button("Cancel", key=f"confirm_no_person_{person}"):
                            del st.session_state[f"confirm_person_{person}"]
                            st.rerun()

# ── Page: Video Upload ────────────────────────────────────────────────────────
def page_video_upload():
    st.markdown('<div class="lens-title">Video Upload</div>', unsafe_allow_html=True)
    st.markdown('<div class="lens-subtitle">Upload and manage videos for face recognition analysis</div>', unsafe_allow_html=True)

    # ── Upload form ───────────────────────────────────────────────────────────
    st.markdown('<div class="video-section-header">Upload new video</div>', unsafe_allow_html=True)
    videos = st.file_uploader(
        "Drop videos here",
        type=["mp4", "mov", "avi", "mkv"],
        accept_multiple_files=True,
        label_visibility="collapsed"
    )

    if videos:
        st.markdown('<div class="video-section-header">Queued for upload</div>', unsafe_allow_html=True)
        for v in videos:
            size_mb = v.size / (1024 * 1024)
            st.markdown(f"""
            <div class="result-card" style="padding:0.6rem 1rem;display:flex;
                        justify-content:space-between;align-items:center;">
                <span style="font-size:0.85rem;color:#374151;">▸ {v.name}</span>
                <span style="font-size:0.78rem;color:#9ca3af;">{size_mb:.1f} MB</span>
            </div>""", unsafe_allow_html=True)

    if st.button("Upload & Analyze", disabled=not videos):
        for video in videos:
            filename = video.name.replace(" ", "_")
            s3_key   = f"videos/{filename}"
            file_size = video.size

            st.markdown(f"**Uploading:** `{filename}` ({file_size / (1024*1024):.1f} MB)")
            progress_bar  = st.progress(0)
            status_text   = st.empty()
            uploaded_bytes = [0]

            def make_callback(bar, txt, total, tracker):
                def callback(bytes_transferred):
                    tracker[0] += bytes_transferred
                    pct = min(int(tracker[0] / total * 100), 100)
                    bar.progress(pct)
                    txt.caption(f"{tracker[0] / (1024*1024):.1f} MB / {total / (1024*1024):.1f} MB  ({pct}%)")
                return callback

            s3.upload_fileobj(
                video, BUCKET, s3_key,
                Config=VIDEO_TRANSFER_CONFIG,
                Callback=make_callback(progress_bar, status_text, file_size, uploaded_bytes)
            )

            progress_bar.progress(100)
            status_text.caption(f"Done! {file_size / (1024*1024):.1f} MB uploaded.")

            sqs.send_message(
                QueueUrl=QUEUE_URL,
                MessageBody=json.dumps({
                    "video_id":  filename,
                    "bucket":    BUCKET,
                    "video_key": s3_key
                })
            )
            st.toast(f"Queued: {filename}")

        st.session_state.polling_videos = [v.name.replace(" ", "_") for v in videos]
        st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Existing videos with delete ───────────────────────────────────────────
    existing = get_s3_videos()
    has_active_jobs = False
    
    if existing:
        st.markdown('<div class="video-section-header">Uploaded videos</div>', unsafe_allow_html=True)
        for video in existing:
            status = get_job_status(video)
            if status == "COMPLETE":
                border, pill_class = "#16a34a", "pill-complete"
                label = "complete"
            elif status == "PROCESSING":
                border, pill_class = "#3b82f6", "pill-processing"
                label = "processing"
                has_active_jobs = True
            elif status == "FAILED":
                border, pill_class = "#dc2626", "pill-failed"
                label = "failed"
            else:
                border, pill_class = "#e5e7eb", "pill-pending"
                label = "queued"
                has_active_jobs = True

            col1, col2 = st.columns([11, 1])
            with col1:
                st.markdown(f"""
                <div class="result-card" style="border-left:3px solid {border};
                            padding:0.6rem 1rem;display:flex;align-items:center;gap:0.75rem;margin-bottom:0;">
                    <span class="status-pill {pill_class}">{label}</span>
                    <span style="font-size:0.875rem;color:#374151;">{video}</span>
                </div>""", unsafe_allow_html=True)
            with col2:
                if st.button("🗑", key=f"del_{video}"):
                    st.session_state[f"confirm_{video}"] = True
                    st.rerun()

            if st.session_state.get(f"confirm_{video}"):
                st.markdown(f"""
                <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;
                            padding:0.6rem 1rem;font-size:0.85rem;color:#dc2626;margin:0.25rem 0 0.5rem 0;">
                    Delete <strong>{video}</strong> and all its results?
                </div>""", unsafe_allow_html=True)

                c1, c2, _ = st.columns([1, 1, 6])
                with c1:
                    if st.button("Delete", key=f"confirm_yes_{video}"):
                        with st.spinner(f"Deleting {video}..."):
                            delete_video(video)
                        if f"confirm_{video}" in st.session_state:
                            del st.session_state[f"confirm_{video}"]
                        st.success(f"Deleted {video}.")
                        time.sleep(1)
                        st.rerun()
                with c2:
                    if st.button("Cancel", key=f"confirm_no_{video}"):
                        del st.session_state[f"confirm_{video}"]
                        st.rerun()

    # ── Auto-refresh / manual refresh at the bottom of the page ────────────────
    if has_active_jobs or ("polling_videos" in st.session_state and st.session_state.polling_videos):
        st.markdown("<br>", unsafe_allow_html=True)
        col_ref, col_info = st.columns([1, 5])
        with col_ref:
            if st.button("Refresh"):
                st.rerun()
        with col_info:
            st.caption("Auto-refreshing every 5 seconds while jobs are active")

        time.sleep(5)
        if "polling_videos" in st.session_state:
            remaining = [v for v in st.session_state.polling_videos if get_job_status(v) not in ("COMPLETE", "FAILED")]
            if remaining:
                st.session_state.polling_videos = remaining
            else:
                del st.session_state.polling_videos
        st.rerun()

# ── Page: Results Dashboard ───────────────────────────────────────────────────
def page_results():
    st.markdown('<div class="lens-title">Results Dashboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="lens-subtitle">Cross-reference identities across video footage</div>', unsafe_allow_html=True)

    people = get_enrolled_people()
    videos = get_s3_videos()

    if not people:
        st.info("No identities enrolled. Go to Face Library.")
        return
    if not videos:
        st.info("No videos uploaded. Go to Video Upload.")
        return

    # ── Sidebar filters ───────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("---")
        st.markdown("**Identities**")
        selected_people = []
        for person in people:
            if st.checkbox(person, key=f"person_{person}"):
                selected_people.append(person)

        st.markdown("**Videos**")
        selected_videos = []
        for video in videos:
            label = video if len(video) < 22 else video[:19] + "..."
            if st.checkbox(label, key=f"video_{video}"):
                selected_videos.append(video)

    if not selected_people or not selected_videos:
        st.markdown("""
        <div style="margin-top:3rem;text-align:center;color:#d1d5db;
                    font-size:0.85rem;letter-spacing:0.05em;">
            Select identities and videos from the left panel
        </div>""", unsafe_allow_html=True)
        return

    # ── Fetch and filter results ──────────────────────────────────────────────
    with st.spinner("Querying recognition database..."):
        all_items = []
        for person in selected_people:
            all_items.extend(get_results(person))

    filtered_items = [
        item for item in all_items
        if item["video_timestamp"].split("#")[0] in selected_videos
    ]

    # ── Stats row ─────────────────────────────────────────────────────────────
    total_appearances = len(filtered_items)
    found_combos = sum(
        1 for p in selected_people for v in selected_videos
        if any(
            i["person"] == p and i["video_timestamp"].split("#")[0] == v
            for i in filtered_items
        )
    )
    total_combos = len(selected_people) * len(selected_videos)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{total_appearances}</div><div class="stat-label">Appearances</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{found_combos}/{total_combos}</div><div class="stat-label">Found</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{len(selected_people)}</div><div class="stat-label">Identities</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{len(selected_videos)}</div><div class="stat-label">Videos</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Results grid: person × video ──────────────────────────────────────────
    for person in selected_people:
        st.markdown(f'<div class="person-header">▸ {person.upper()}</div>', unsafe_allow_html=True)

        for video_id in selected_videos:
            appearances = sorted([
                {**item, "start_time": item["video_timestamp"].split("#")[1]}
                for item in filtered_items
                if item["person"] == person
                and item["video_timestamp"].split("#")[0] == video_id
            ], key=lambda x: x["start_time"])

            st.markdown(f'<div class="video-section-header">📹 {video_id}</div>', unsafe_allow_html=True)

            if not appearances:
                st.markdown(f'<div class="not-found-card">✗ &nbsp; {person} not detected in {video_id}</div>', unsafe_allow_html=True)
            else:
                for app in appearances:
                    col1, col2 = st.columns([1, 2])
                    with col1:
                        url = get_presigned_url(app.get("screenshot_url", ""))
                        if url:
                            st.image(url, width='stretch')
                        else:
                            st.markdown("""
                            <div style="background:#f3f4f6;border:1px solid #e5e7eb;border-radius:6px;
                                        height:120px;display:flex;align-items:center;
                                        justify-content:center;font-size:0.75rem;color:#9ca3af;">
                                No screenshot
                            </div>""", unsafe_allow_html=True)
                    with col2:
                        conf       = float(app['confidence'])
                        conf_color = "#16a34a" if conf >= 95 else "#d97706" if conf >= 90 else "#dc2626"
                        st.markdown(f"""
                        <div style="padding:0.5rem 0;">
                            <span class="timestamp-badge">{app['start_time']}</span>
                            <span style="color:#9ca3af;font-size:0.75rem;">→</span>
                            <span class="timestamp-badge">{app['end_time']}</span>
                            <br><br>
                            <span style="font-size:0.75rem;color:#9ca3af;">CONFIDENCE &nbsp;</span>
                            <span style="font-size:1.1rem;font-weight:600;color:{conf_color};">{conf:.1f}%</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown('<hr>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

# ── App ───────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FaceTrace",
    layout="wide",
    initial_sidebar_state="expanded"
)

inject_css()
page = render_sidebar()

if page == "Face Library":
    page_face_library()
elif page == "Video Upload":
    page_video_upload()
else:
    page_results()