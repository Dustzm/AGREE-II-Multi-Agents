# -*- coding: utf-8 -*-
"""
Benchmark API 路由

提供 Benchmark 对比功能的 Web API 接口
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Benchmark 相关导入
import sys
# 从 web/backend/app/api/routes/benchmark.py 往上 6 级到项目根目录
project_root = Path(__file__).parent.parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from benchmark.benchmark import Benchmark
from benchmark.metrics.consistency import ConsistencyMetric
from benchmark.reporter import ExcelReporter

logger = logging.getLogger(__name__)

router = APIRouter()

# ===== 目录配置 =====

def get_benchmark_dir() -> Path:
    """获取 benchmark 根目录"""
    return project_root / "benchmark"


def get_uploads_dir() -> Path:
    """获取上传文件目录"""
    uploads_dir = get_benchmark_dir() / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


def get_output_dir() -> Path:
    """获取结果输出目录"""
    output_dir = get_benchmark_dir() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_model_output_dir() -> Path:
    """获取模型评估输出目录（包含 batch_* 子目录）"""
    return project_root / "output"


# ===== 数据模型 =====

class HumanFileInfo(BaseModel):
    """人类评分文件信息"""
    name: str
    path: str
    size: int
    modified: str


class BatchInfo(BaseModel):
    """Batch 目录信息"""
    name: str
    path: str
    task_count: int
    created: str


class RunBenchmarkRequest(BaseModel):
    """执行 Benchmark 请求"""
    human_file: str           # 人类评分文件名
    batch_ids: List[str]      # 选择的两个 batch 目录名
    threshold: int = 2        # 一致性阈值


class GuideConsistency(BaseModel):
    """指南一致性数据"""
    seq_no: int
    guide_id: str
    guide_name: str
    consistent_count: int
    total_matches: int
    consistency: float
    status: str


class ItemConsistency(BaseModel):
    """条目一致性数据"""
    item_id: str
    item_name: str
    consistent_count: int
    total_matches: int
    consistency: float


class BenchmarkSummary(BaseModel):
    """Benchmark 汇总数据（用于可视化）"""
    total_guides: int
    success_count: int
    overall_consistency: float
    guide_consistencies: List[GuideConsistency]
    item_consistencies: List[ItemConsistency]


class BenchmarkResultInfo(BaseModel):
    """Benchmark 结果文件信息"""
    name: str
    path: str
    size: int
    created: str
    human_file: Optional[str] = None
    batch_ids: Optional[List[str]] = None


class RunBenchmarkResponse(BaseModel):
    """执行 Benchmark 响应"""
    status: str
    message: str
    xlsx_file: Optional[str] = None
    download_url: Optional[str] = None
    summary: Optional[BenchmarkSummary] = None
    error: Optional[str] = None


# ===== API 路由 =====

@router.get("/benchmark/human-files")
async def list_human_files():
    """
    列出已上传的人类评分文件
    """
    uploads_dir = get_uploads_dir()
    files = []
    
    for file_path in uploads_dir.glob("*.xlsx"):
        stat = file_path.stat()
        files.append(HumanFileInfo(
            name=file_path.name,
            path=str(file_path),
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        ))
    
    # 按修改时间倒序
    files.sort(key=lambda x: x.modified, reverse=True)
    
    return {"files": files}


@router.post("/benchmark/human-files")
async def upload_human_file(file: UploadFile = File(...)):
    """
    上传人类评分 xlsx 文件
    """
    if not file.filename.endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="只支持 .xlsx 文件")
    
    uploads_dir = get_uploads_dir()
    file_path = uploads_dir / file.filename
    
    # 保存文件
    try:
        content = await file.read()
        with open(file_path, 'wb') as f:
            f.write(content)
        
        logger.info(f"上传人类评分文件: {file.filename}")
        
        return {
            "status": "success",
            "message": f"文件 {file.filename} 上传成功",
            "file": {
                "name": file.filename,
                "path": str(file_path),
                "size": len(content),
            }
        }
    except Exception as e:
        logger.error(f"上传文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.get("/benchmark/batches")
async def list_batches():
    """
    列出 output 下可用的 batch 目录
    """
    output_dir = get_model_output_dir()
    batches = []
    
    if not output_dir.exists():
        return {"batches": []}
    
    for batch_path in sorted(output_dir.iterdir(), reverse=True):
        if batch_path.is_dir() and batch_path.name.startswith('batch_'):
            # 统计任务目录数量
            task_count = len([
                d for d in batch_path.iterdir()
                if d.is_dir() and not d.name.startswith('.')
            ])
            
            stat = batch_path.stat()
            
            batches.append(BatchInfo(
                name=batch_path.name,
                path=str(batch_path),
                task_count=task_count,
                created=datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            ))
    
    return {"batches": batches}


@router.post("/benchmark/run", response_model=RunBenchmarkResponse)
async def run_benchmark(request: RunBenchmarkRequest):
    """
    执行 Benchmark 对比
    
    1. 加载人类评分文件
    2. 加载选定的两个 batch 目录
    3. 执行对比计算
    4. 生成 Excel 报告
    5. 返回汇总数据（JSON）
    """
    # 验证参数
    if len(request.batch_ids) != 2:
        raise HTTPException(status_code=400, detail="必须选择恰好两个 batch 目录")
    
    # 验证人类评分文件
    uploads_dir = get_uploads_dir()
    human_file_path = uploads_dir / request.human_file
    if not human_file_path.exists():
        raise HTTPException(status_code=404, detail=f"人类评分文件不存在: {request.human_file}")
    
    # 验证 batch 目录
    output_dir = get_model_output_dir()
    for batch_id in request.batch_ids:
        batch_path = output_dir / batch_id
        if not batch_path.exists():
            raise HTTPException(status_code=404, detail=f"Batch 目录不存在: {batch_id}")
    
    try:
        # 创建 Benchmark 实例（传入指定的 batch_dirs）
        benchmark = Benchmark(str(human_file_path), str(output_dir))
        # 重新初始化 model_loader 以使用指定的 batch_dirs
        from benchmark.loader import BatchModelScoreLoader
        benchmark.model_loader = BatchModelScoreLoader(str(output_dir), request.batch_ids)
        
        # 注册指标
        benchmark.register_metric(ConsistencyMetric(threshold=request.threshold))
        
        # 执行对比
        logger.info(f"开始执行 Benchmark: human={request.human_file}, batches={request.batch_ids}")
        results = benchmark.run()
        
        # 生成报告文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        xlsx_filename = f"benchmark_{timestamp}.xlsx"
        xlsx_path = get_output_dir() / xlsx_filename
        
        # 生成 Excel 报告
        reporter = ExcelReporter()
        reporter.generate(results, str(xlsx_path))
        
        # 构建汇总数据
        guide_consistencies = []
        item_stats = {f"item_{i}": {"consistent": 0, "total": 0} for i in range(1, 24)}
        total_consistent = 0
        total_matches = 0
        success_count = 0
        
        for result in results:
            consistency = result.metrics.get('consistency_ratio')
            
            guide_data = GuideConsistency(
                seq_no=result.guide_entry.seq_no,
                guide_id=result.guide_entry.guide_id,
                guide_name=result.guide_entry.guide_name,
                consistent_count=consistency.details.get('consistent_count', 0) if consistency else 0,
                total_matches=consistency.details.get('total_matches', 92) if consistency else 92,
                consistency=consistency.value if consistency else 0,
                status="success" if result.success else "error",
            )
            guide_consistencies.append(guide_data)
            
            if result.success and consistency:
                success_count += 1
                total_consistent += consistency.details.get('consistent_count', 0)
                total_matches += consistency.details.get('total_matches', 92)
                
                # 统计条目一致性
                items = consistency.details.get('items', {})
                for item_id, item_detail in items.items():
                    if item_detail.get('status') == 'ok':
                        item_stats[item_id]['consistent'] += item_detail.get('consistent_count', 0)
                        item_stats[item_id]['total'] += 4  # 每个指南4次匹配
        
        # 构建条目一致性列表
        item_consistencies = []
        for i in range(1, 24):
            item_id = f"item_{i}"
            stats = item_stats[item_id]
            total = stats['total']
            item_consistencies.append(ItemConsistency(
                item_id=item_id,
                item_name=f"条目{i}",
                consistent_count=stats['consistent'],
                total_matches=total,
                consistency=stats['consistent'] / total if total > 0 else 0,
            ))
        
        overall_consistency = total_consistent / total_matches if total_matches > 0 else 0
        
        summary = BenchmarkSummary(
            total_guides=len(results),
            success_count=success_count,
            overall_consistency=overall_consistency,
            guide_consistencies=guide_consistencies,
            item_consistencies=item_consistencies,
        )
        
        # 保存元数据（可选，用于结果列表显示）
        meta_path = xlsx_path.with_suffix('.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump({
                "human_file": request.human_file,
                "batch_ids": request.batch_ids,
                "threshold": request.threshold,
                "created": datetime.now().isoformat(),
                "summary": summary.dict(),
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Benchmark 完成: {xlsx_filename}, 一致性: {overall_consistency:.2%}")
        
        return RunBenchmarkResponse(
            status="success",
            message=f"对比完成，共 {success_count}/{len(results)} 个指南成功",
            xlsx_file=xlsx_filename,
            download_url=f"/api/benchmark/download/{xlsx_filename}",
            summary=summary,
        )
        
    except Exception as e:
        logger.exception(f"Benchmark 执行失败: {e}")
        return RunBenchmarkResponse(
            status="error",
            message="执行失败",
            error=str(e),
        )


@router.get("/benchmark/results")
async def list_benchmark_results():
    """
    列出 benchmark 输出结果
    """
    output_dir = get_output_dir()
    results = []
    
    for xlsx_path in sorted(output_dir.glob("*.xlsx"), reverse=True):
        stat = xlsx_path.stat()
        
        # 尝试加载元数据
        meta_path = xlsx_path.with_suffix('.json')
        human_file = None
        batch_ids = None
        if meta_path.exists():
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    human_file = meta.get('human_file')
                    batch_ids = meta.get('batch_ids')
            except Exception:
                pass
        
        results.append(BenchmarkResultInfo(
            name=xlsx_path.name,
            path=str(xlsx_path),
            size=stat.st_size,
            created=datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M"),
            human_file=human_file,
            batch_ids=batch_ids,
        ))
    
    return {"results": results}


@router.get("/benchmark/download/{filename}")
async def download_benchmark_result(filename: str):
    """
    下载 benchmark 结果文件
    """
    output_dir = get_output_dir()
    file_path = output_dir / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.delete("/benchmark/human-files/{filename}")
async def delete_human_file(filename: str):
    """
    删除已上传的人类评分文件
    """
    uploads_dir = get_uploads_dir()
    file_path = uploads_dir / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
    
    try:
        file_path.unlink()
        logger.info(f"删除人类评分文件: {filename}")
        return {"status": "success", "message": f"文件 {filename} 已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")
