# Specification: Benchmark Result Editor & Persistence

## 1. Goal
允许用户在前端界面直接修改 Benchmark 的评分数据，修改后的数据能够高亮显示（类似 Git Diff），并持久化保存到后端的 JSON 文件中。

## 2. Core Features
### 2.1 Score Editing
- **交互方式**: 点击评分单元格可进入编辑模式（或直接替换为 Input）。
- **数据验证**: 评分限制在 1-7 分之间（参考 AGREE-II 标准）。

### 2.2 Visual Feedback (Highligting)
- **单元格高亮**: 当单元格的值与原始值不一致时，显示明显的边框或背景色（如黄色或红色边框）。
- **侧边栏高亮**: 当某个指南内部存在任何被修改的评分时，左侧侧边栏对应的指南卡片应显示“修改”状态（如高亮背景或圆点标记）。

### 2.3 Persistence
- **保存操作**: 界面顶部提供“保存”按钮。
- **后端存储**: 将修改后的数据回写到原始的 JSON 文件中（`benchmark/output/benchmark_*.json`）。

## 3. Technical Design
### 3.1 Backend (FastAPI)
- **API**: `POST /api/benchmark/update`
- **Request Body**:
  ```json
  {
    "filename": "benchmark_20260119_114511.json",
    "updates": [
      {
        "guide_id": "1-2023-014E",
        "item_id": 1,
        "scorer": "model_20260119_112001",
        "new_score": 5
      }
    ]
  }
  ```
  *(或者直接发送全量 JSON，取决于文件大小和并发需求。考虑到单机使用，发送全量或覆盖后的完整 `guide_data` 可能更简单)*

### 3.2 Frontend (React)
- **State Management**: 使用 `useImmer` 或 `useState` 维护一份 `modifiedData` 副本。
- **Diff Logic**: 渲染时对比 `originalData` 和 `modifiedData`。
