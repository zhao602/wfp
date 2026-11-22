// =============================================================================
// Embedding 绑定控件（web/src/knowledge_base/EmbeddingBindingControl.tsx）
//
// 职责：Embedding 模型候选渲染 + 空候选手工填写（T8.8 CHG-29）。
// 设计意图（INV-D10 / AC-44 / AC-45）：
//   1. 零凭据：不渲染任何凭据字段（无服务地址 / 密钥字段位）；
//   2. 候选来源 GET /kb-management/embedding-models（响应字段白名单）；
//      拉取失败 → 候选回落空 + 允许手工填写（表单不阻断）；
//   3. 空候选 → allow_manual_input=true 时呈现手工填写区（§5.5.3-1）。
// =============================================================================

import { useEffect, useState } from 'react';
import { useThemeMode, darkTokens } from '../theme/themeMode';
import { kbManagementApiClient } from '../api/kbManagementApiClient';
import type {
  EmbeddingBindingMode,
  EmbeddingCandidateItem,
} from './kbManagementTypes';

/** 绑定草稿（控件输出形态：零凭据，仅绑定必要字段） */
export interface EmbeddingBindingDraft {
  mode: EmbeddingBindingMode;
  model_identifier: string;
  allow_degraded_fallback: boolean;
}

/** Embedding 绑定控件属性 */
export interface EmbeddingBindingControlProps {
  value: EmbeddingBindingDraft;
  onChange: (value: EmbeddingBindingDraft) => void;
}

/** Embedding 绑定控件：候选选择 / 手工填写 */
export function EmbeddingBindingControl({
  value,
  onChange,
}: EmbeddingBindingControlProps): JSX.Element {
  const themeMode = useThemeMode();
  const isDark = themeMode === 'dark';
  const [candidates, setCandidates] = useState<EmbeddingCandidateItem[]>([]);
  const [allowManualInput, setAllowManualInput] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    kbManagementApiClient
      .listEmbeddingModels()
      .then((response) => {
        if (cancelled) return;
        setCandidates(response.candidates);
        setAllowManualInput(response.allow_manual_input);
        setError(null);
      })
      .catch(() => {
        // 候选拉取失败：回落空候选 + 允许手工填写，不阻断表单
        if (cancelled) return;
        setCandidates([]);
        setAllowManualInput(true);
        setError('Embedding 模型候选拉取失败，可手工填写模型标识');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="wfp-kbm-binding" data-testid="embedding-binding-control">
      <style>{`
.wfp-kbm-binding{display:flex;flex-direction:column;gap:8px;}
.wfp-kbm-binding-hint{font-size:12px;color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-binding-error{font-size:12px;color:${isDark ? darkTokens.danger : '#d32f2f'};}
.wfp-kbm-candidate{display:flex;align-items:center;gap:8px;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;cursor:pointer;transition:background-color 120ms ease,border-color 120ms ease;}
.wfp-kbm-candidate:hover{background-color:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'};}
.wfp-kbm-candidate--active{border-color:${isDark ? darkTokens.surface.borderStrong : '#1a1a1a'};background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};}
.wfp-kbm-candidate-mode{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;background:${isDark ? darkTokens.surface.bgActive : '#f0f0f0'};color:${isDark ? darkTokens.text.secondary : '#616161'};}
.wfp-kbm-manual{display:flex;flex-direction:column;gap:8px;padding:12px;background:${isDark ? darkTokens.surface.bgHover : '#fafafa'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:8px;}
.wfp-kbm-manual-title{font-size:13px;font-weight:600;margin:0;color:${isDark ? darkTokens.text.primary : '#1a1a1a'};}
.wfp-kbm-manual-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.wfp-kbm-input{flex:1;min-width:160px;box-sizing:border-box;padding:7px 10px;font-size:13px;font-weight:500;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-input:focus{border-color:${isDark ? darkTokens.surface.borderStrong : '#bdbdbd'};}
.wfp-kbm-select{padding:7px 10px;font-size:13px;font-family:inherit;color:${isDark ? darkTokens.text.primary : '#333333'};background-color:${isDark ? darkTokens.surface.bg : '#ffffff'};border:1px solid ${isDark ? darkTokens.surface.divider : '#e0e0e0'};border-radius:5px;outline:none;}
.wfp-kbm-check{display:flex;align-items:center;gap:6px;font-size:12px;color:${isDark ? darkTokens.text.secondary : '#616161'};cursor:pointer;}
`}</style>

      <span className="wfp-kbm-binding-hint">Embedding 模型（绑定后用于检索与写入向量化）</span>
      {error && (
        <div className="wfp-kbm-binding-error" data-testid="embedding-candidates-error">
          {error}
        </div>
      )}

      {/* 候选列表（响应字段白名单：model_identifier / mode / label，零凭据） */}
      {candidates.length > 0 && (
        <div className="wfp-kbm-binding-candidates" data-testid="embedding-candidates">
          {candidates.map((candidate) => {
            const active =
              value.mode === candidate.mode &&
              value.model_identifier === candidate.model_identifier;
            return (
              <div
                key={`${candidate.mode}:${candidate.model_identifier}`}
                className={`wfp-kbm-candidate${active ? ' wfp-kbm-candidate--active' : ''}`}
                onClick={() =>
                  onChange({
                    mode: candidate.mode,
                    model_identifier: candidate.model_identifier,
                    allow_degraded_fallback: value.allow_degraded_fallback,
                  })
                }
                data-testid={`embedding-candidate-${candidate.model_identifier}`}
                role="radio"
                aria-checked={active}
              >
                <span>{candidate.label ?? candidate.model_identifier}</span>
                <span className="wfp-kbm-candidate-mode">{candidate.mode}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* 手工填写区（allow_manual_input=true 时呈现；空候选必现） */}
      {allowManualInput && (
        <div className="wfp-kbm-manual" data-testid="embedding-manual-input">
          <p className="wfp-kbm-manual-title">手工填写模型标识</p>
          <div className="wfp-kbm-manual-row">
            <select
              className="wfp-kbm-select"
              value={value.mode}
              onChange={(e) =>
                onChange({
                  mode: e.target.value === 'api' ? 'api' : 'local',
                  model_identifier: value.model_identifier,
                  allow_degraded_fallback: value.allow_degraded_fallback,
                })
              }
              data-testid="manual-mode-select"
            >
              <option value="local">local（本地模型）</option>
              <option value="api">api（API 服务）</option>
            </select>
            <input
              className="wfp-kbm-input"
              type="text"
              placeholder="模型标识（如 bge-small-zh）"
              value={value.model_identifier}
              onChange={(e) =>
                onChange({
                  mode: value.mode,
                  model_identifier: e.target.value,
                  allow_degraded_fallback: value.allow_degraded_fallback,
                })
              }
              data-testid="manual-model-input"
            />
          </div>
          <label className="wfp-kbm-check">
            <input
              type="checkbox"
              checked={value.allow_degraded_fallback}
              onChange={(e) =>
                onChange({
                  mode: value.mode,
                  model_identifier: value.model_identifier,
                  allow_degraded_fallback: e.target.checked,
                })
              }
              data-testid="manual-fallback-checkbox"
            />
            允许降级兜底（Embedding 不可用时哈希降级）
          </label>
        </div>
      )}
    </div>
  );
}
