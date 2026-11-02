"""
Generic cross-chunk object store for rule-engine inference.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict

from core.rule_engine.models import (
    ItemSpec,
    ObjectStoreSnapshot,
    ReviewReason,
    RuleObject,
)
from core.rule_engine.text_normalization import contains_source_span


class ObjectStore:
    """Merge, deduplicate, and validate extracted rule objects."""

    def __init__(self, spec: ItemSpec):
        self.spec = spec
        self._objects_by_key: OrderedDict[tuple[str, str, str, str], RuleObject] = OrderedDict()
        self._processed_chunks: set[str] = set()
        self._chunk_order: dict[str, int] = {}
        self._review_reasons: list[ReviewReason] = []

    def merge(
        self,
        objects: list[RuleObject],
        processed_chunks: list[str] | None = None,
        review_reasons: list[ReviewReason] | None = None,
        chunk_text_by_id: dict[str, str] | None = None,
    ) -> None:
        """Merge extracted objects and remember processed chunks."""
        if processed_chunks:
            for chunk_id in processed_chunks:
                if chunk_id not in self._chunk_order:
                    self._chunk_order[chunk_id] = len(self._chunk_order)
            self._processed_chunks.update(processed_chunks)
        if review_reasons:
            self._review_reasons.extend(review_reasons)

        for obj in objects:
            if obj.type not in self.spec.object_specs:
                self._review_reasons.append(
                    ReviewReason(
                        code="unknown_object_type",
                        message=f"抽取到未在 ItemSpec 中声明的对象类型: {obj.type}",
                        object_type=obj.type,
                        object_value=obj.value,
                    )
                )
                continue

            obj_spec = self.spec.object_specs[obj.type]
            if obj_spec.evidence_required and not obj.evidence.quote.strip():
                self._review_reasons.append(
                    ReviewReason(
                        code="missing_quote",
                        message=f"{obj.type} 缺少原文 quote，已排除出计分对象",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )
                continue

            if chunk_text_by_id is not None:
                chunk_text = chunk_text_by_id.get(obj.evidence.chunk_id)
                if chunk_text is None:
                    self._review_reasons.append(
                        ReviewReason(
                            code="unknown_quote_chunk",
                            message=f"{obj.type} 引用的 chunk_id 不在当前批次原文中，已排除出计分对象",
                            object_type=obj.type,
                            object_value=obj.value,
                            severity="warning",
                        )
                    )
                    continue
                if not contains_source_span(chunk_text, obj.evidence.quote):
                    self._review_reasons.append(
                        ReviewReason(
                            code="quote_not_found_in_chunk",
                            message=f"{obj.type} 的 quote 未在对应 chunk 原文中出现，已排除出计分对象",
                            object_type=obj.type,
                            object_value=obj.value,
                            severity="warning",
                        )
                    )
                    continue

            key = self._dedupe_key(obj)
            if key not in self._objects_by_key:
                self._objects_by_key[key] = obj

    def snapshot(self) -> ObjectStoreSnapshot:
        """Return an immutable snapshot with validation review reasons."""
        objects, limit_reasons = self._limit_objects(tuple(self._objects_by_key.values()))
        review_reasons = list(self._review_reasons)
        review_reasons.extend(limit_reasons)
        review_reasons.extend(self._validate_objects(objects))
        return ObjectStoreSnapshot(
            item_id=self.spec.item_id,
            objects=objects,
            processed_chunks=tuple(sorted(self._processed_chunks)),
            chunk_order=tuple(sorted(self._chunk_order.items(), key=lambda item: item[1])),
            review_reasons=tuple(review_reasons),
        )

    def _dedupe_key(self, obj: RuleObject) -> tuple[str, str, str, str]:
        return (
            obj.type.strip().lower(),
            obj.value.strip().lower(),
            obj.evidence.chunk_id.strip(),
            obj.evidence.quote.strip(),
        )

    def _validate_objects(self, objects: tuple[RuleObject, ...]) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []

        for obj in objects:
            obj_spec = self.spec.object_specs[obj.type]
            if obj.confidence < obj_spec.min_confidence:
                reasons.append(
                    ReviewReason(
                        code="low_confidence_object",
                        message=f"{obj.type} 置信度低于阈值 {obj_spec.min_confidence}",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )

        return reasons

    def _limit_objects(self, objects: tuple[RuleObject, ...]) -> tuple[tuple[RuleObject, ...], list[ReviewReason]]:
        """Apply per-object-type representative evidence limits."""
        chunk_order = dict(self._chunk_order)
        indexed_objects = list(enumerate(objects))
        objects_by_type: dict[str, list[tuple[int, RuleObject]]] = defaultdict(list)
        for original_index, obj in indexed_objects:
            objects_by_type[obj.type].append((original_index, obj))

        kept_by_index: dict[int, tuple[int, RuleObject]] = {}
        reasons: list[ReviewReason] = []
        for object_type, candidates in objects_by_type.items():
            obj_spec = self.spec.object_specs[object_type]
            max_objects = obj_spec.max_objects
            if max_objects is None or len(candidates) <= max_objects:
                for index, obj in candidates:
                    kept_by_index[index] = (index, obj)
                continue

            if max_objects < 1:
                selected: list[tuple[int, RuleObject]] = []
            else:
                selected = sorted(
                    candidates,
                    key=lambda item: self._limit_sort_key(item, chunk_order),
                )[:max_objects]
            for index, obj in selected:
                kept_by_index[index] = (index, obj)
            reasons.append(
                ReviewReason(
                    code="object_limit_exceeded",
                    message=f"{object_type} 超出代表证据上限 {max_objects}，已仅保留最强证据",
                    object_type=object_type,
                    severity="warning",
                )
            )

        limited_objects = tuple(
            obj
            for _, obj in sorted(
                kept_by_index.values(),
                key=lambda item: (
                    item[1].type,
                    self._limit_sort_key(item, chunk_order),
                ),
            )
        )
        return limited_objects, reasons

    def _limit_sort_key(
        self,
        item: tuple[int, RuleObject],
        chunk_order: dict[str, int],
    ) -> tuple[float, int, int, int]:
        original_index, obj = item
        return (
            -obj.confidence,
            chunk_order.get(obj.evidence.chunk_id, len(chunk_order)),
            len(obj.evidence.quote.strip()),
            original_index,
        )
