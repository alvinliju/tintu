from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import subprocess
import re
from pathlib import Path

app = FastAPI(title="CourseMap API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
REPO_PATH = "./coursemap-content"  # Git repo folder
COURSES_DIR = f"{REPO_PATH}/courses"
INDEX_FILE = f"{REPO_PATH}/index.json"

# Initialize repo structure
def init_repo():
    os.makedirs(COURSES_DIR, exist_ok=True)
    if not os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'w') as f:
            json.dump({"courses": []}, f, indent=2)

    # Initialize git if not exists
    if not os.path.exists(f"{REPO_PATH}/.git"):
        subprocess.run(["git", "init"], cwd=REPO_PATH)
        subprocess.run(["git", "config", "user.name", "CourseMap"], cwd=REPO_PATH)
        subprocess.run(["git", "config", "user.email", "coursemap@local"], cwd=REPO_PATH)

init_repo()

# Helpers
def slugify(text):
    """Convert text to URL-safe slug"""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text

def git_commit_push(message):
    """Git add, commit, and push"""
    try:
        subprocess.run(["git", "add", "."], cwd=REPO_PATH, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=REPO_PATH, check=True)
        # Only push if remote exists
        result = subprocess.run(["git", "remote"], cwd=REPO_PATH, capture_output=True, text=True)
        if result.stdout.strip():
            subprocess.run(["git", "push"], cwd=REPO_PATH, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}")
        return False

def load_index():
    with open(INDEX_FILE, 'r') as f:
        return json.load(f)

def save_index(data):
    with open(INDEX_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_course_meta(course_slug):
    """Load course metadata from course.json"""
    meta_file = f"{COURSES_DIR}/{course_slug}/course.json"
    if os.path.exists(meta_file):
        with open(meta_file, 'r') as f:
            return json.load(f)
    return None

def save_course_meta(course_slug, data):
    """Save course metadata to course.json"""
    course_dir = f"{COURSES_DIR}/{course_slug}"
    os.makedirs(course_dir, exist_ok=True)
    meta_file = f"{course_dir}/course.json"
    with open(meta_file, 'w') as f:
        json.dump(data, f, indent=2)

def calculate_progress(course_slug):
    """Calculate course progress based on completed topics"""
    meta = get_course_meta(course_slug)
    if not meta:
        return 0

    total = 0
    completed = 0
    for module in meta.get("modules", []):
        for topic in module.get("topics", []):
            total += 1
            if topic.get("completed", False):
                completed += 1

    return int((completed / total * 100) if total > 0 else 0)

# Models
class CourseCreate(BaseModel):
    code: str
    name: str

class ModuleCreate(BaseModel):
    title: str

class TopicCreate(BaseModel):
    title: str
    priority: str = "medium"
    time: int = 15

class TopicUpdate(BaseModel):
    content: Optional[str] = None
    completed: Optional[bool] = None

# Routes

@app.get("/")
def root():
    return {"status": "CourseMap API (Git-backed)", "repo": REPO_PATH}

@app.get("/courses")
def get_courses():
    """List all courses from index.json"""
    index = load_index()
    courses = []

    for course_ref in index.get("courses", []):
        course_slug = course_ref["slug"]
        meta = get_course_meta(course_slug)
        if meta:
            meta["progress"] = calculate_progress(course_slug)
            courses.append(meta)

    return courses

@app.post("/courses")
def create_course(course: CourseCreate):
    """Create new course"""
    course_slug = slugify(f"{course.code}-{course.name}")
    course_dir = f"{COURSES_DIR}/{course_slug}"

    if os.path.exists(course_dir):
        raise HTTPException(status_code=400, detail="Course already exists")

    os.makedirs(course_dir, exist_ok=True)

    # Create course metadata
    course_id = len(load_index().get("courses", [])) + 1
    meta = {
        "id": course_id,
        "code": course.code,
        "name": course.name,
        "slug": course_slug,
        "progress": 0,
        "modules": []
    }
    save_course_meta(course_slug, meta)

    # Update index
    index = load_index()
    index["courses"].append({"id": course_id, "slug": course_slug})
    save_index(index)

    # Git commit
    git_commit_push(f"Create course: {course.code} - {course.name}")

    return meta

@app.get("/courses/{course_id}")
def get_course(course_id: int):
    """Get course by ID"""
    index = load_index()
    course_ref = next((c for c in index["courses"] if c["id"] == course_id), None)
    if not course_ref:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = get_course_meta(course_ref["slug"])
    if not meta:
        raise HTTPException(status_code=404, detail="Course metadata not found")

    meta["progress"] = calculate_progress(course_ref["slug"])
    return meta

@app.post("/courses/{course_id}/modules")
def create_module(course_id: int, module: ModuleCreate):
    """Create new module in course"""
    index = load_index()
    course_ref = next((c for c in index["courses"] if c["id"] == course_id), None)
    if not course_ref:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = get_course_meta(course_ref["slug"])
    module_slug = slugify(module.title)
    module_dir = f"{COURSES_DIR}/{course_ref['slug']}/{module_slug}"
    os.makedirs(module_dir, exist_ok=True)

    # Add module to metadata
    module_id = len(meta.get("modules", [])) + 1
    new_module = {
        "id": module_id,
        "title": module.title,
        "slug": module_slug,
        "completed": False,
        "topics": []
    }
    meta["modules"].append(new_module)
    save_course_meta(course_ref["slug"], meta)

    git_commit_push(f"Add module: {module.title}")

    return new_module

@app.post("/courses/{course_id}/modules/{module_id}/topics")
def create_topic(course_id: int, module_id: int, topic: TopicCreate):
    """Create new topic in module"""
    index = load_index()
    course_ref = next((c for c in index["courses"] if c["id"] == course_id), None)
    if not course_ref:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = get_course_meta(course_ref["slug"])
    module = next((m for m in meta["modules"] if m["id"] == module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    # Create markdown file
    topic_slug = slugify(topic.title)
    topic_file = f"{COURSES_DIR}/{course_ref['slug']}/{module['slug']}/{topic_slug}.md"

    # Initialize with template
    template = f"""# {topic.title}

## Overview
Write your content here...

## Key Concepts

## Examples

## Practice Problems
"""

    with open(topic_file, 'w') as f:
        f.write(template)

    # Add topic to metadata
    topic_id = len(module.get("topics", [])) + 1
    new_topic = {
        "id": topic_id,
        "title": topic.title,
        "slug": topic_slug,
        "file": f"{module['slug']}/{topic_slug}.md",
        "completed": False,
        "priority": topic.priority,
        "time": topic.time,
        "locked": False
    }
    module["topics"].append(new_topic)
    save_course_meta(course_ref["slug"], meta)

    git_commit_push(f"Add topic: {topic.title}")

    return new_topic

@app.get("/courses/{course_id}/modules/{module_id}/topics/{topic_id}")
def get_topic(course_id: int, module_id: int, topic_id: int):
    """Get topic with content from markdown file"""
    index = load_index()
    course_ref = next((c for c in index["courses"] if c["id"] == course_id), None)
    if not course_ref:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = get_course_meta(course_ref["slug"])
    module = next((m for m in meta["modules"] if m["id"] == module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    topic = next((t for t in module["topics"] if t["id"] == topic_id), None)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Read markdown file
    topic_file = f"{COURSES_DIR}/{course_ref['slug']}/{topic['file']}"
    content = ""
    if os.path.exists(topic_file):
        with open(topic_file, 'r') as f:
            content = f.read()

    return {**topic, "content": content}

@app.patch("/courses/{course_id}/modules/{module_id}/topics/{topic_id}")
def update_topic(course_id: int, module_id: int, topic_id: int, updates: TopicUpdate):
    """Update topic content or completion status"""
    index = load_index()
    course_ref = next((c for c in index["courses"] if c["id"] == course_id), None)
    if not course_ref:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = get_course_meta(course_ref["slug"])
    module = next((m for m in meta["modules"] if m["id"] == module_id), None)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")

    topic = next((t for t in module["topics"] if t["id"] == topic_id), None)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Update content if provided
    if updates.content is not None:
        topic_file = f"{COURSES_DIR}/{course_ref['slug']}/{topic['file']}"
        with open(topic_file, 'w') as f:
            f.write(updates.content)
        git_commit_push(f"Update topic: {topic['title']}")

    # Update completion status
    if updates.completed is not None:
        topic["completed"] = updates.completed
        save_course_meta(course_ref["slug"], meta)
        git_commit_push(f"Mark {'complete' if updates.completed else 'incomplete'}: {topic['title']}")

    # Read current content
    topic_file = f"{COURSES_DIR}/{course_ref['slug']}/{topic['file']}"
    content = ""
    if os.path.exists(topic_file):
        with open(topic_file, 'r') as f:
            content = f.read()

    return {**topic, "content": content}

@app.get("/search")
def search(q: str):
    """Search across all content"""
    results = []
    q_lower = q.lower()

    index = load_index()
    for course_ref in index.get("courses", []):
        meta = get_course_meta(course_ref["slug"])
        if not meta:
            continue

        # Search in course
        if q_lower in meta["code"].lower() or q_lower in meta["name"].lower():
            results.append({
                "type": "course",
                "course_id": meta["id"],
                "title": f"{meta['code']} - {meta['name']}"
            })

        # Search in modules and topics
        for module in meta.get("modules", []):
            if q_lower in module["title"].lower():
                results.append({
                    "type": "module",
                    "course_id": meta["id"],
                    "module_id": module["id"],
                    "title": f"{meta['code']} > {module['title']}"
                })

            for topic in module.get("topics", []):
                if q_lower in topic["title"].lower():
                    # Also search in file content
                    topic_file = f"{COURSES_DIR}/{course_ref['slug']}/{topic['file']}"
                    if os.path.exists(topic_file):
                        with open(topic_file, 'r') as f:
                            content = f.read()
                            if q_lower in content.lower():
                                results.append({
                                    "type": "topic",
                                    "course_id": meta["id"],
                                    "module_id": module["id"],
                                    "topic_id": topic["id"],
                                    "title": f"{meta['code']} > {module['title']} > {topic['title']}"
                                })

    return results[:20]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
