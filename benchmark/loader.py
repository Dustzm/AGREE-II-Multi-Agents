# -*- coding: utf-8 -*-
"""
数据加载模块

负责从Excel和JSON文件加载评分数据。
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

import pandas as pd

logger = logging.getLogger(__name__)

FINAL_REPORT_FILENAMES = ("appraisal_report.json", "report.json")


@dataclass
class GuideEntry:
    """目录条目"""
    seq_no: int           # 序号（A列）
    guide_id: str         # 指南编号（D列）
    guide_name: str       # 指南名称（B列）


@dataclass
class HumanScore:
    """人类评分"""
    item_id: str          # 条目ID，如 "item_1"
    score1: int           # 评分者1
    score2: int           # 评分者2
    
    @property
    def average(self) -> float:
        """平均分"""
        return (self.score1 + self.score2) / 2


@dataclass
class ModelScore:
    """模型评分"""
    item_id: str          # 条目ID，如 "item_1"
    score: int            # 评分
    metadata: Dict[str, Any] = field(default_factory=dict)


class HumanScoreLoader:
    """从多sheet Excel加载人类评分"""

    def __init__(self, excel_path: str):
        """
        初始化加载器

        Args:
            excel_path: Excel文件路径
        """
        self.excel_path = Path(excel_path)
        self._xlsx: Optional[pd.ExcelFile] = None
        self._rater_names: Optional[List[str]] = None

    @property
    def xlsx(self) -> pd.ExcelFile:
        """延迟加载Excel文件"""
        if self._xlsx is None:
            self._xlsx = pd.ExcelFile(self.excel_path)
        return self._xlsx

    def get_rater_names(self, seq_no: int = None) -> List[str]:
        """
        获取评分者姓名

        从指定指南 sheet 的第 2 行 C、D 列读取评分者姓名
        如果未指定 seq_no，则从第一个指南 sheet 读取

        Args:
            seq_no: 指南序号，如果为 None 则使用第一个指南

        Returns:
            评分者姓名列表，如 ['吴之仪', '曾传飞']
        """
        if self._rater_names is not None:
            return self._rater_names

        # 确定要读取的 sheet
        if seq_no is None:
            # 从第一个指南 sheet 读取
            entries = self.load_index()
            if not entries:
                logger.warning("没有找到指南条目，无法获取评分者姓名")
                self._rater_names = []
                return self._rater_names
            seq_no = entries[0].seq_no

        # 尝试多种 sheet 命名格式
        possible_names = [
            str(seq_no),           # "5"
            f"指南{seq_no}",       # "指南5"
            f"Sheet{seq_no}",      # "Sheet5"
        ]

        sheet_name = None
        for name in possible_names:
            if name in self.xlsx.sheet_names:
                sheet_name = name
                break

        if sheet_name is None:
            logger.warning(f"Sheet '{seq_no}' 不存在，无法获取评分者姓名")
            self._rater_names = []
            return self._rater_names

        # 读取第 2 行（索引 1）获取评分者姓名
        df = pd.read_excel(self.xlsx, sheet_name=sheet_name, header=None)
        if len(df) < 2:
            logger.warning(f"Sheet {sheet_name} 数据不足，无法获取评分者姓名")
            self._rater_names = []
            return self._rater_names

        header_row = df.iloc[1]  # 第 2 行
        rater1_name = str(header_row.iloc[2]) if pd.notna(header_row.iloc[2]) else ""  # C 列
        rater2_name = str(header_row.iloc[3]) if pd.notna(header_row.iloc[3]) else ""  # D 列

        self._rater_names = [rater1_name, rater2_name]
        logger.info(f"获取评分者姓名: {self._rater_names}")
        return self._rater_names

    def load_index(self) -> List[GuideEntry]:
        """
        从Sheet1加载目录页
        
        Returns:
            指南条目列表
        """
        df = pd.read_excel(self.xlsx, sheet_name='Sheet1')
        entries = []
        
        for _, row in df.iterrows():
            try:
                entry = GuideEntry(
                    seq_no=int(row['序号']),
                    guide_id=str(row['指南编号']),
                    guide_name=str(row['指南名称']) if '指南名称' in row else ""
                )
                entries.append(entry)
            except (ValueError, KeyError) as e:
                logger.warning(f"跳过无效行: {e}")
                continue
        
        logger.info(f"加载了 {len(entries)} 个指南条目")
        return entries
    
    def load_scores(self, seq_no: int) -> Dict[str, HumanScore]:
        """
        根据序号加载对应Sheet的评分数据
        
        Args:
            seq_no: 序号（对应Sheet名，支持 "5" 或 "指南5" 格式）
            
        Returns:
            条目评分字典 {item_id: HumanScore}
        """
        # 尝试多种Sheet命名格式
        possible_names = [
            str(seq_no),           # "5"
            f"指南{seq_no}",       # "指南5"
            f"Sheet{seq_no}",      # "Sheet5"
        ]
        
        sheet_name = None
        for name in possible_names:
            if name in self.xlsx.sheet_names:
                sheet_name = name
                break
        
        if sheet_name is None:
            raise ValueError(f"Sheet '{seq_no}' 不存在（尝试了: {possible_names}）")
        
        df = pd.read_excel(self.xlsx, sheet_name=sheet_name)
        scores = {}
        
        for _, row in df.iterrows():
            item_name = row.iloc[1]  # B列条目名
            
            # 只处理"条目X"格式的行
            if pd.notna(item_name) and str(item_name).startswith('条目'):
                item_num = str(item_name).replace('条目', '')
                item_id = f"item_{item_num}"
                
                try:
                    # 只检查评分者1和2（C、D列）
                    score1_val = row.iloc[2]  # C列: 评分者1
                    score2_val = row.iloc[3]  # D列: 评分者2
                    
                    if pd.isna(score1_val) or pd.isna(score2_val):
                        logger.warning(f"条目 {item_id} 包含空值，跳过")
                        continue
                    
                    score = HumanScore(
                        item_id=item_id,
                        score1=int(score1_val),
                        score2=int(score2_val),
                    )
                    scores[item_id] = score
                except (ValueError, IndexError) as e:
                    logger.warning(f"解析条目 {item_id} 失败: {e}")
                    continue
        
        logger.info(f"从Sheet {seq_no} 加载了 {len(scores)} 个条目评分")
        return scores


class ModelScoreLoader:
    """从output目录加载模型评分"""
    
    def __init__(self, output_dir: str, batch_id: Optional[str] = None):
        """
        初始化加载器
        
        Args:
            output_dir: output目录路径（可以是batch子目录或根目录）
            batch_id: 批次标识，用于区分不同批次的结果
        """
        self.output_dir = Path(output_dir)
        self.batch_id = batch_id

    def _has_final_report(self, task_dir: Path) -> bool:
        """判断任务目录是否已生成最终报告。"""
        return task_dir.is_dir() and any(
            (task_dir / report_name).exists() for report_name in FINAL_REPORT_FILENAMES
        )
    
    def find_latest_task(self, guide_id: str) -> Optional[Path]:
        """
        根据指南编号查找最新的任务目录
        
        Args:
            guide_id: 指南编号（如 "1-2012-006E"）
            
        Returns:
            最新任务目录路径，如果未找到返回None
        """
        matching_dirs: list[Path] = []

        # 兼容新目录格式：output/<batch>/<guide_id>/
        exact_dir = self.output_dir / guide_id
        if self._has_final_report(exact_dir):
            matching_dirs.append(exact_dir)

        # 兼容旧目录格式：output/<batch>/<guide_id>-<timestamp>/
        legacy_dirs = [
            task_dir
            for task_dir in self.output_dir.glob(f"{guide_id}-*")
            if self._has_final_report(task_dir)
        ]
        legacy_dirs.sort(key=lambda path: path.name)

        for legacy_dir in legacy_dirs:
            if legacy_dir not in matching_dirs:
                matching_dirs.append(legacy_dir)

        if not matching_dirs:
            logger.warning(f"未找到指南 {guide_id} 的最终报告任务目录")
            return None

        # 优先使用新格式的精确目录；否则回退到旧格式中名称最大的一个。
        latest_dir = matching_dirs[0] if matching_dirs[0].name == guide_id else matching_dirs[-1]
        logger.info(f"找到最新任务目录: {latest_dir.name}")
        
        return latest_dir
    
    def load_scores(self, task_dir: Path) -> Dict[str, ModelScore]:
        """
        从任务目录加载所有条目评分
        
        Args:
            task_dir: 任务目录路径
            
        Returns:
            条目评分字典 {item_id: ModelScore}
        """
        eval_dir = task_dir / ".ama" / "evaluations"
        
        if not eval_dir.exists():
            raise ValueError(f"评估目录不存在: {eval_dir}")
        
        scores = {}
        
        for json_file in eval_dir.glob("item_*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                item_id = data.get('item_id', json_file.stem)
                
                # 从chain_of_thought中提取评分
                chain = data.get('chain_of_thought', {})
                score_value = chain.get('score')
                
                if score_value is None:
                    logger.warning(f"文件 {json_file.name} 缺少评分")
                    continue
                
                score = ModelScore(
                    item_id=item_id,
                    score=int(score_value),
                    metadata={
                        'domain': data.get('domain'),
                        'evaluated_at': data.get('metadata', {}).get('evaluated_at'),
                        'batch_id': self.batch_id
                    }
                )
                scores[item_id] = score
                
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"解析文件 {json_file.name} 失败: {e}")
                continue
        
        logger.info(f"从 {task_dir.name} 加载了 {len(scores)} 个模型评分")
        return scores


class BatchModelScoreLoader:
    """从output目录加载多批次模型评分"""
    
    def __init__(self, output_dir: str, batch_dirs: Optional[List[str]] = None):
        """
        初始化加载器
        
        Args:
            output_dir: output根目录路径
            batch_dirs: 指定的批次目录名列表，为 None 则自动发现所有 batch_* / cot_* 目录
        """
        self.output_dir = Path(output_dir)
        self._loaders: Dict[str, ModelScoreLoader] = {}
        
        # 如果未指定，则自动发现所有 batch_* / cot_* 目录
        if batch_dirs is None:
            batch_dirs = sorted([
                d.name for d in self.output_dir.iterdir()
                if d.is_dir() and (d.name.startswith('batch_') or d.name.startswith('cot_'))
            ]) if self.output_dir.exists() else []
        
        # 初始化各批次加载器
        for batch_id in batch_dirs:
            batch_path = self.output_dir / batch_id
            if batch_path.exists():
                self._loaders[batch_id] = ModelScoreLoader(str(batch_path), batch_id)
                logger.info(f"初始化批次加载器: {batch_id}")
            else:
                logger.warning(f"批次目录不存在: {batch_path}")
    
    @property
    def available_batches(self) -> List[str]:
        """获取可用的批次ID列表"""
        return list(self._loaders.keys())
    
    def find_latest_tasks(self, guide_id: str) -> Dict[str, Optional[Path]]:
        """
        根据指南编号查找各批次的最新任务目录
        
        Args:
            guide_id: 指南编号
            
        Returns:
            {batch_id: task_dir} 字典
        """
        result = {}
        for batch_id, loader in self._loaders.items():
            result[batch_id] = loader.find_latest_task(guide_id)
        return result
    
    def load_all_scores(self, guide_id: str) -> Dict[str, Dict[str, ModelScore]]:
        """
        加载指定指南的所有批次评分
        
        Args:
            guide_id: 指南编号
            
        Returns:
            {batch_id: {item_id: ModelScore}} 嵌套字典
        """
        result = {}
        task_dirs = self.find_latest_tasks(guide_id)
        
        for batch_id, task_dir in task_dirs.items():
            if task_dir is None:
                logger.warning(f"批次 {batch_id} 未找到指南 {guide_id} 的评估结果")
                result[batch_id] = {}
                continue
            
            try:
                result[batch_id] = self._loaders[batch_id].load_scores(task_dir)
            except Exception as e:
                logger.error(f"加载批次 {batch_id} 评分失败: {e}")
                result[batch_id] = {}
        
        return result
