"""Shared setup for query-time code: project root, .env, HF cache location."""

from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
load_dotenv(ROOT / ".env")
# HF_HOME in .env is relative to the project root, not to wherever the
# process happens to start. Resolve it before transformers reads it.
if os.getenv("HF_HOME") and not os.path.isabs(os.environ["HF_HOME"]):
    os.environ["HF_HOME"] = str((ROOT / os.environ["HF_HOME"]).resolve())
