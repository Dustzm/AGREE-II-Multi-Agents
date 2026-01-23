# Implementation Plan - Benchmark Editor

## Phase 1: Backend API & Data Structure
- [ ] Task: Analyze JSON structure
    - [ ] Read full content of a benchmark JSON file to confirm where item-level scores are stored.
    - [ ] Create a backup of the test JSON file.
- [ ] Task: Implement Update API
    - [ ] Create Pydantic models for the update request.
    - [ ] Implement `POST /api/benchmark/save` in `web/backend/app/api/routes/benchmark.py`.
    - [ ] Ensure the endpoint writes atomic updates to the JSON file.

## Phase 2: Frontend State & Interaction
- [ ] Task: Setup Edit State in GuideDetailsPanel
    - [ ] Add state to track modified scores locally.
    - [ ] Create helper functions to check `isModified(guideId, itemId, scorer)`.
- [ ] Task: Implement Editable Cell Component
    - [ ] Create an input component that replaces the text score on click.
    - [ ] Add validation (1-7 range).
    - [ ] Apply highlighting styles (e.g., `ring-2 ring-orange-400`) when value differs from original.

## Phase 3: Sidebar & Global UX
- [ ] Task: Sidebar Highlighting
    - [ ] Update sidebar list rendering to check for modified guides.
    - [ ] Apply visual indicator (e.g., purple border or dot) to modified guides.
- [ ] Task: Save Action
    - [ ] Add a floating or top-bar "Save Changes" button.
    - [ ] Connect save button to the backend API.
    - [ ] Add toast notification on success.

## Phase 4: Verification
- [ ] Task: Verify Persistence
    - [ ] Edit a score, refresh page, verify score remains changed.
    - [ ] Check JSON file on disk to ensure correctness.
