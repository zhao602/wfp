// =============================================================================
// 向量知识库节点配置面板（src/canvas/panels/vector_knowledge_base/index.tsx）
//
// 职责：vector_knowledge_base 节点类型专属配置表单（模型加载子表单/知识库绑定
// 摘要/向量模式与检索参数/写入模式分片参数等，CHG-15），由 NodeConfigurationPanel
// 按 isVectorKnowledgeBaseConfig 分发进入（T1.2 独立分支拆分）。
// 来源：自 NodeConfigurationPanel.tsx 纯搬运，代码零语义变化，仅补 export。
// =============================================================================

import { useEffect, useState } from "react";
import type { ModelLoadingConfig, ModelLoadingMode, VectorKnowledgeBaseNodeConfiguration, VectorNodeMode, WriteSourceMode, WriteChunkParams } from "../../../workflow-definition/definitionTypes";
import type { KnowledgeBaseView, ChunkStrategy } from "../../../knowledge_base/kbManagementTypes";
import type { WorkflowCanvasState } from "../../canvasTypes";
import { kbManagementApiClient } from "../../../api/kbManagementApiClient";
import { resolveFieldHint, buildHintId } from "../../nodeConfigHints";
import { isVectorKbModeFieldVisible, isVectorKnowledgeBaseFieldVisible } from "../../nodeConfigFieldVisibility";
import KnowledgeBaseSelectionList from "../../KnowledgeBaseSelectionList";
import KnowledgeBaseBindingSummary from "../../KnowledgeBaseBindingSummary";
import VariableReferenceSelector from "../../VariableReferenceSelector";
import { Field, HintAnchor } from "../shared/Field";

// ---------------------------------------------------------------------------
// 向量知识库节点配置表单（vector_knowledge_base，CHG-15）
// ---------------------------------------------------------------------------

/**
 * 模型加载配置子表单（rerank_model 专用；embedding 区已改只读展示，CHG-33/DC-08）。
 * 按加载方式互斥展示：api → 服务地址 + 超时；local → 模型路径 + 推理设备 + 降级兜底开关。
 * 模型级提示经 field 键挂在"加载方式"字段下方，
 * 子字段未登记提示时按 Field 缺省结构渲染（label + 控件，无提示区）。
 */
export function ModelLoadingConfigFields({
  field,
  model,
  onModelChange,
}: {
  field: "embedding_model" | "rerank_model";
  model: ModelLoadingConfig;
  onModelChange: (model: ModelLoadingConfig) => void;
}) {
  return (
    <>
      <Field nodeType="vector_knowledge_base" field={field} label="加载方式">
        <select
          className="wfp-ncp-select"
          value={model.mode}
          onChange={(e) =>
            onModelChange({ ...model, mode: e.target.value as ModelLoadingMode })
          }
        >
          <option value="api">API 服务</option>
          <option value="local">本地运行时</option>
        </select>
      </Field>
      <Field nodeType="vector_knowledge_base" field={`${field}_model_identifier`} label="模型标识">
        <input
          type="text"
          className="wfp-ncp-input"
          value={model.model_identifier}
          onChange={(e) => onModelChange({ ...model, model_identifier: e.target.value })}
        />
      </Field>
      {model.mode === "api" ? (
        <>
          <Field nodeType="vector_knowledge_base" field={`${field}_base_url`} label="服务地址">
            <input
              type="text"
              className="wfp-ncp-input"
              value={model.base_url ?? ""}
              onChange={(e) => onModelChange({ ...model, base_url: e.target.value })}
            />
          </Field>
          <Field nodeType="vector_knowledge_base" field={`${field}_timeout_seconds`} label="超时（秒）">
            <input
              type="number"
              className="wfp-ncp-input"
              min={1}
              value={model.timeout_seconds ?? ""}
              onChange={(e) => {
                const parsed = Number(e.target.value);
                if (!Number.isFinite(parsed)) {
                  onModelChange({ ...model, timeout_seconds: undefined });
                  return;
                }
                onModelChange({
                  ...model,
                  timeout_seconds: Math.max(1, Math.round(parsed)),
                });
              }}
            />
          </Field>
        </>
      ) : (
        <>
          <Field nodeType="vector_knowledge_base" field={`${field}_model_path`} label="模型路径">
            <input
              type="text"
              className="wfp-ncp-input"
              value={model.model_path ?? ""}
              onChange={(e) => onModelChange({ ...model, model_path: e.target.value })}
            />
          </Field>
          <Field nodeType="vector_knowledge_base" field={`${field}_device`} label="推理设备">
            <select
              className="wfp-ncp-select"
              value={model.device ?? "cpu"}
              onChange={(e) =>
                onModelChange({
                  ...model,
                  device: e.target.value as NonNullable<ModelLoadingConfig["device"]>,
                })
              }
            >
              <option value="cpu">cpu</option>
              <option value="gpu">gpu</option>
            </select>
          </Field>
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              marginBottom: "8px",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={model.allow_degraded_fallback ?? false}
              onChange={(e) =>
                onModelChange({
                  ...model,
                  allow_degraded_fallback: e.target.checked,
                })
              }
            />
            依赖缺失时允许降级兜底
          </label>
        </>
      )}
    </>
  );
}

export function VectorKnowledgeBaseConfigForm({
  config,
  onChange,
  canvasState,
  targetNodeId,
}: {
  config: VectorKnowledgeBaseNodeConfiguration;
  onChange: (config: VectorKnowledgeBaseNodeConfiguration) => void;
  canvasState: WorkflowCanvasState;
  targetNodeId?: string;
}) {
  /** 自动检测的已配置知识库清单（挂载单次拉取，active 标志丢弃卸载后迟到响应） */
  const [kbCatalog, setKbCatalog] = useState<KnowledgeBaseView[]>([]);
  const [kbMenuOpen, setKbMenuOpen] = useState(false);
  useEffect(() => {
    let active = true;
    kbManagementApiClient
      .listKnowledgeBases(1, 50)
      .then((res) => {
        if (active) setKbCatalog(res.items);
      })
      .catch(() => {
        /* 后台开关未启用/网络异常：静默降级，下拉候选不渲染，自由文本输入不受影响 */
      });
    return () => {
      active = false;
    };
  }, []);
  const kbIdHint = resolveFieldHint("vector_knowledge_base", "knowledge_base_id");
  return (
    <div style={{ fontSize: "12px" }}>
      {/* 知识库标识：自由文本 + 已配置知识库下拉候选（自动检测）。
          不走 Field 包装：组合框为复合控件，Field 的 decorateControl 仅装饰
          单一原生元素（aria-describedby 会落到容器 div，破坏 input 挂钩契约），
          故按 Field 同构 DOM 手工搭建（label > 字段标签 + 控件区 + 字段提示）。 */}
      <label className="wfp-ncp-field">
        <span className="wfp-ncp-labelrow">
          <span className="wfp-ncp-field-label">知识库标识</span>
          {kbIdHint?.helper !== undefined && (
            <HintAnchor
              id={buildHintId("vector_knowledge_base", "knowledge_base_id")}
              text={kbIdHint.helper}
            />
          )}
        </span>
        <span className="wfp-ncp-control">
          <div className="wfp-kbid-combo">
            <input
              type="text"
              className={
                kbCatalog.length > 0 ? "wfp-ncp-input wfp-kbid-input" : "wfp-ncp-input"
              }
              value={config.knowledge_base_id}
              placeholder={kbIdHint?.placeholder}
              aria-describedby={buildHintId("vector_knowledge_base", "knowledge_base_id")}
              onFocus={() => {
                if (kbCatalog.length > 0) setKbMenuOpen(true);
              }}
              onBlur={() => {
                window.setTimeout(() => setKbMenuOpen(false), 150);
              }}
              onChange={(e) => onChange({ ...config, knowledge_base_id: e.target.value })}
            />
            {kbCatalog.length > 0 && (
              <button
                type="button"
                className="wfp-kbid-trigger"
                title="选择已配置的知识库"
                aria-label="选择已配置的知识库"
                aria-haspopup="listbox"
                aria-expanded={kbMenuOpen}
                onClick={() => setKbMenuOpen(!kbMenuOpen)}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="8"
                  height="5"
                  viewBox="0 0 8 5"
                  aria-hidden="true"
                >
                  <path d="M0 0 L4 5 L8 0 Z" fill="currentColor" />
                </svg>
              </button>
            )}
            {kbMenuOpen && kbCatalog.length > 0 && (
              <div className="wfp-kbid-menu">
                {kbCatalog.map((kb) => (
                  <button
                    type="button"
                    key={kb.knowledge_base_id}
                    className={
                      kb.knowledge_base_id === config.knowledge_base_id
                        ? "wfp-kbid-option wfp-kbid-option-selected"
                        : "wfp-kbid-option"
                    }
                    title={kb.knowledge_base_id}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onChange({ ...config, knowledge_base_id: kb.knowledge_base_id });
                      setKbMenuOpen(false);
                    }}
                  >
                    <span className="wfp-kbid-option-id">{kb.knowledge_base_id}</span>
                    <span className="wfp-kbid-option-name">{kb.name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </span>
      </label>
      {/* 从后台知识库清单选择（AC-47，DC-08/CHG-33）：受控单值候选卡；
          拉取失败（开关未启用）整块降级不渲染，回退上方自由文本（零破坏） */}
      <KnowledgeBaseSelectionList
        value={config.knowledge_base_id}
        onChange={(kbId) => onChange({ ...config, knowledge_base_id: kbId })}
      />
      {/* vkb_write_mode：节点模式切换（检索/写入）；检索/写入字段组按模式互斥显隐（AC-09） */}
      <Field nodeType="vector_knowledge_base" field="mode" label="节点模式">
        <select
          className="wfp-ncp-select"
          value={config.mode ?? "retrieve"}
          onChange={(e) =>
            onChange({ ...config, mode: e.target.value as VectorNodeMode })
          }
        >
          <option value="retrieve">检索</option>
          <option value="write">写入</option>
        </select>
      </Field>
      {/* 检索字段组：仅检索模式可见（可见性规则单一来源见 nodeConfigFieldVisibility） */}
      {isVectorKbModeFieldVisible("query_mode", config) && (
        <>
      <Field nodeType="vector_knowledge_base" field="query_mode" label="查询录入模式">
        <select
          className="wfp-ncp-select"
          value={config.query_mode}
          onChange={(e) =>
            onChange({
              ...config,
              query_mode: e.target.value as VectorKnowledgeBaseNodeConfiguration["query_mode"],
            })
          }
        >
          <option value="manual">手动填写</option>
          <option value="reference">继承上游</option>
        </select>
      </Field>
      {/* 查询文本：仅手动录入模式可见（可见性规则单一来源见 nodeConfigFieldVisibility，AC-06） */}
      {isVectorKnowledgeBaseFieldVisible("query_value", config) && (
        <Field nodeType="vector_knowledge_base" field="query_value" label="查询文本">
          <textarea
            className="wfp-ncp-input"
            value={config.query_value ?? ""}
            onChange={(e) => onChange({ ...config, query_value: e.target.value })}
            rows={3}
            maxLength={2000}
            style={{ width: "100%" }}
          />
        </Field>
      )}
      {/* 查询引用：仅继承上游模式可见，复用既有上游引用选择器（props 不变，AC-42/FR-25） */}
      {isVectorKnowledgeBaseFieldVisible("query_reference", config) && (
        <Field nodeType="vector_knowledge_base" field="query_reference" label="查询引用">
          <VariableReferenceSelector
            canvasState={canvasState}
            value={config.query_reference ?? ""}
            onChange={(v) => onChange({ ...config, query_reference: v })}
            targetNodeId={targetNodeId}
          />
        </Field>
      )}
      <Field nodeType="vector_knowledge_base" field="top_k" label="召回数量上限">
        <input
          type="number"
          className="wfp-ncp-input"
          min={1}
          max={100}
          value={config.top_k}
          onChange={(e) => {
            const parsed = Number(e.target.value);
            if (!Number.isFinite(parsed)) return;
            onChange({
              ...config,
              top_k: Math.min(100, Math.max(1, Math.round(parsed))),
            });
          }}
        />
      </Field>
        </>
      )}
      {/* 写入字段组：仅写入模式可见（vkb_write_mode） */}
      {isVectorKbModeFieldVisible("write_source_mode", config) && (
        <>
          <Field nodeType="vector_knowledge_base" field="write_source_mode" label="待写入文本录入模式">
            <select
              className="wfp-ncp-select"
              value={config.write_source_mode ?? "manual"}
              onChange={(e) =>
                onChange({
                  ...config,
                  write_source_mode: e.target.value as WriteSourceMode,
                })
              }
            >
              <option value="manual">手动填写</option>
              <option value="reference">继承上游</option>
            </select>
          </Field>
          {isVectorKbModeFieldVisible("write_text", config) && (
            <Field nodeType="vector_knowledge_base" field="write_text" label="待写入文本">
              <textarea
                className="wfp-ncp-input"
                value={config.write_text ?? ""}
                onChange={(e) => onChange({ ...config, write_text: e.target.value })}
                rows={3}
                style={{ width: "100%" }}
              />
            </Field>
          )}
          {isVectorKbModeFieldVisible("write_reference", config) && (
            <Field nodeType="vector_knowledge_base" field="write_reference" label="待写入文本引用">
              <VariableReferenceSelector
                canvasState={canvasState}
                value={config.write_reference ?? ""}
                onChange={(v) => onChange({ ...config, write_reference: v })}
                targetNodeId={targetNodeId}
              />
            </Field>
          )}
          <Field nodeType="vector_knowledge_base" field="write_chunk_strategy" label="分片策略（可选）">
            <select
              className="wfp-ncp-select"
              value={config.write_chunk_strategy ?? ""}
              onChange={(e) =>
                onChange({
                  ...config,
                  write_chunk_strategy: e.target.value
                    ? (e.target.value as ChunkStrategy)
                    : undefined,
                })
              }
            >
              <option value="">（缺省：取知识库默认）</option>
              <option value="paragraph">paragraph</option>
              <option value="fixed_length">fixed_length</option>
              <option value="recursive">recursive</option>
              <option value="structural">structural</option>
              <option value="semantic">semantic</option>
            </select>
          </Field>
          <Field nodeType="vector_knowledge_base" field="write_chunk_params" label="分片参数（可选）">
            <WriteChunkParamsFields
              params={config.write_chunk_params}
              onParamsChange={(params) =>
                onChange({ ...config, write_chunk_params: params })
              }
            />
          </Field>
          <Field nodeType="vector_knowledge_base" field="write_document_id" label="写入幂等键（可选）">
            <input
              type="text"
              className="wfp-ncp-input"
              value={config.write_document_id ?? ""}
              placeholder="缺省按「知识库:节点」生成（同键覆盖）"
              onChange={(e) =>
                onChange({ ...config, write_document_id: e.target.value })
              }
            />
          </Field>
          {/* vkb_doc_annotation：切片标注配置（来源/发布时间/自定义/开关，CHG-29） */}
          <Field nodeType="vector_knowledge_base" field="annotation_origin" label="标注-来源（可选）">
            <input
              type="text"
              className="wfp-ncp-input"
              value={config.annotation_origin ?? ""}
              placeholder="缺省「工作流写入」"
              onChange={(e) =>
                onChange({ ...config, annotation_origin: e.target.value })
              }
            />
          </Field>
          <Field nodeType="vector_knowledge_base" field="annotation_published_at" label="标注-发布时间（可选）">
            <input
              type="text"
              className="wfp-ncp-input"
              value={config.annotation_published_at ?? ""}
              placeholder="ISO-8601，留空=未知"
              onChange={(e) =>
                onChange({
                  ...config,
                  annotation_published_at:
                    e.target.value === "" ? null : e.target.value,
                })
              }
            />
          </Field>
          <Field nodeType="vector_knowledge_base" field="annotation_custom" label="标注-自定义（JSON，可选）">
            <textarea
              className="wfp-ncp-input"
              rows={3}
              style={{ width: "100%" }}
              value={
                config.annotation_custom
                  ? JSON.stringify(config.annotation_custom)
                  : ""
              }
              placeholder='例如 {"版本":"v2"}'
              onChange={(e) => {
                const raw = e.target.value;
                if (raw.trim() === "") {
                  onChange({ ...config, annotation_custom: undefined });
                  return;
                }
                try {
                  const parsed = JSON.parse(raw) as unknown;
                  if (
                    parsed !== null &&
                    typeof parsed === "object" &&
                    !Array.isArray(parsed)
                  ) {
                    onChange({
                      ...config,
                      annotation_custom: parsed as Record<string, unknown>,
                    });
                  }
                } catch {
                  /* 非法 JSON：不写回（保持上次有效值） */
                }
              }}
            />
          </Field>
          <Field nodeType="vector_knowledge_base" field="annotation_enabled" label="标注能力（可选）">
            <select
              className="wfp-ncp-select"
              value={
                config.annotation_enabled === undefined
                  ? ""
                  : config.annotation_enabled
                    ? "true"
                    : "false"
              }
              onChange={(e) => {
                const raw = e.target.value;
                onChange({
                  ...config,
                  annotation_enabled: raw === "" ? undefined : raw === "true",
                });
              }}
            >
              <option value="">跟随全局开关</option>
              <option value="true">启用</option>
              <option value="false">禁用</option>
            </select>
          </Field>
        </>
      )}
      <div style={{ marginTop: "6px", marginBottom: "4px" }}>
          <div className="wfp-ncp-section-label">向量化模型</div>
        {/* 只读展示绑定模型摘要（AC-46/AC-48，DC-08/CHG-33）：移除前台独立选模入口，
            绑定优先、按当前绑定拉取（AC-49）；旧配置遗留 embedding_model 展示覆盖提示 */}
        <Field nodeType="vector_knowledge_base" field="embedding_model" label="绑定模型（只读）">
          <KnowledgeBaseBindingSummary
            knowledgeBaseId={config.knowledge_base_id}
            legacyEmbeddingModel={config.embedding_model}
          />
        </Field>
      </div>
      {isVectorKbModeFieldVisible("rerank_model", config) && (
        <div style={{ marginTop: "6px", marginBottom: "4px" }}>
          <div className="wfp-ncp-section-label">重排模型（可选）</div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            marginBottom: "8px",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={config.rerank_model !== undefined}
            onChange={(e) =>
              onChange({
                ...config,
                rerank_model: e.target.checked
                  ? config.rerank_model ?? { mode: "api", model_identifier: "" }
                  : undefined,
              })
            }
          />
          启用重排
        </label>
        {config.rerank_model && (
          <ModelLoadingConfigFields
            field="rerank_model"
            model={config.rerank_model}
            onModelChange={(model) => onChange({ ...config, rerank_model: model })}
          />
        )}
        </div>
      )}
    </div>
  );
}

/** 写入模式分片参数子表单（vkb_write_mode）：三数字输入 + 全空归一 undefined。 */
export function WriteChunkParamsFields({
  params,
  onParamsChange,
}: {
  params: WriteChunkParams | undefined;
  onParamsChange: (params: WriteChunkParams | undefined) => void;
}) {
  const next = (patch: WriteChunkParams): WriteChunkParams | undefined => {
    const merged: WriteChunkParams = { ...(params ?? {}), ...patch };
    const hasValue =
      merged.max_chunk_length !== undefined ||
      merged.overlap !== undefined ||
      merged.similarity_threshold !== undefined ||
      // L-10（fix_agent_null_length INV-01）：原 !== undefined 对 null 穿透致 .length 崩溃，
      // 改 Array.isArray 真值守卫（fix_datasource_source_type_null_guard 同款回归预防）
      (Array.isArray(merged.hierarchy) && merged.hierarchy.length > 0);
    return hasValue ? merged : undefined;
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <input
        type="number"
        className="wfp-ncp-input"
        placeholder="分片长度上限（缺省 500，50..2000）"
        min={50}
        max={2000}
        value={params?.max_chunk_length ?? ""}
        onChange={(e) => {
          const raw = e.target.value;
          const parsed = Number(raw);
          onParamsChange(
            next({
              max_chunk_length:
                raw !== "" && Number.isFinite(parsed)
                  ? Math.min(2000, Math.max(50, Math.round(parsed)))
                  : undefined,
            }),
          );
        }}
      />
      <input
        type="number"
        className="wfp-ncp-input"
        placeholder="重叠字符数（缺省 50）"
        min={0}
        value={params?.overlap ?? ""}
        onChange={(e) => {
          const raw = e.target.value;
          const parsed = Number(raw);
          onParamsChange(
            next({
              overlap:
                raw !== "" && Number.isFinite(parsed)
                  ? Math.max(0, Math.round(parsed))
                  : undefined,
            }),
          );
        }}
      />
      <input
        type="number"
        className="wfp-ncp-input"
        placeholder="相似度阈值（缺省 0.75，仅 semantic）"
        min={0}
        max={1}
        step={0.01}
        value={params?.similarity_threshold ?? ""}
        onChange={(e) => {
          const raw = e.target.value;
          const parsed = Number(raw);
          onParamsChange(
            next({
              similarity_threshold:
                raw !== "" && Number.isFinite(parsed)
                  ? Math.min(1, Math.max(0, parsed))
                  : undefined,
            }),
          );
        }}
      />
    </div>
  );
}
