// =============================================================================
// Embedding 绑定控件测试（src/knowledge_base/__tests__/EmbeddingBindingControl.test.tsx）
//
// 单文件执行：cd web && npx vitest run src/knowledge_base/__tests__/EmbeddingBindingControl.test.tsx
//
// 覆盖用例（T8.10 / T8.8 CHG-29，INV-D10 / AC-44 / AC-45）：
//   TC-EBC-01 候选渲染 + 点击选择回调
//   TC-EBC-02 空候选 → allow_manual_input=true 手工填写区呈现
//   TC-EBC-03 候选拉取失败 → 降级手工填写（不阻断）+ 错误提示
//   TC-EBC-04 手工字段变更回调（模式 / 标识 / 兜底开关）
//   TC-EBC-05 不渲染凭据字段（无服务地址 / 密钥输入位）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import {
  EmbeddingBindingControl,
} from '../EmbeddingBindingControl';
import type { EmbeddingBindingDraft } from '../EmbeddingBindingControl';
import { kbManagementApiClient } from '../../api/kbManagementApiClient';

/** 初始绑定草稿夹具 */
const INITIAL_DRAFT: EmbeddingBindingDraft = {
  mode: 'local',
  model_identifier: '',
  allow_degraded_fallback: false,
};

beforeEach(() => {
  vi.spyOn(kbManagementApiClient, 'listEmbeddingModels').mockResolvedValue({
    candidates: [
      { model_identifier: 'bge-small-zh', mode: 'local', label: 'BGE 小模型' },
      { model_identifier: 'text-embedding-3-small', mode: 'api' },
    ],
    total: 2,
    allow_manual_input: true,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('EmbeddingBindingControl', () => {
  it('TC-EBC-01 候选渲染 + 点击选择回调', async () => {
    const onChange = vi.fn();
    render(
      <EmbeddingBindingControl value={INITIAL_DRAFT} onChange={onChange} />,
    );

    await screen.findByTestId('embedding-candidates');
    expect(
      screen.getByTestId('embedding-candidate-bge-small-zh'),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId('embedding-candidate-text-embedding-3-small'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('embedding-candidate-bge-small-zh'));
    expect(onChange).toHaveBeenCalledWith({
      mode: 'local',
      model_identifier: 'bge-small-zh',
      allow_degraded_fallback: false,
    });
  });

  it('TC-EBC-02 空候选 → allow_manual_input=true 手工填写区呈现', async () => {
    vi.spyOn(kbManagementApiClient, 'listEmbeddingModels').mockResolvedValue({
      candidates: [],
      total: 0,
      allow_manual_input: true,
    });

    render(
      <EmbeddingBindingControl value={INITIAL_DRAFT} onChange={vi.fn()} />,
    );

    await screen.findByTestId('embedding-manual-input');
    // 空候选：候选区不渲染
    expect(screen.queryByTestId('embedding-candidates')).toBeNull();
    expect(screen.getByTestId('manual-model-input')).toBeInTheDocument();
    expect(screen.getByTestId('manual-mode-select')).toBeInTheDocument();
  });

  it('TC-EBC-03 候选拉取失败 → 降级手工填写（不阻断）+ 错误提示', async () => {
    vi.spyOn(kbManagementApiClient, 'listEmbeddingModels').mockRejectedValue(
      new Error('网络不可达'),
    );

    render(
      <EmbeddingBindingControl value={INITIAL_DRAFT} onChange={vi.fn()} />,
    );

    const errorHint = await screen.findByTestId('embedding-candidates-error');
    expect(errorHint).toHaveTextContent('候选拉取失败');
    // 降级：手工填写区仍可用（表单不阻断）
    expect(screen.getByTestId('embedding-manual-input')).toBeInTheDocument();
  });

  it('TC-EBC-04 手工字段变更回调（模式 / 标识 / 兜底开关）', async () => {
    const onChange = vi.fn();
    render(
      <EmbeddingBindingControl value={INITIAL_DRAFT} onChange={onChange} />,
    );

    await screen.findByTestId('embedding-manual-input');

    // 模式切换 local → api
    fireEvent.change(screen.getByTestId('manual-mode-select'), {
      target: { value: 'api' },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      mode: 'api',
      model_identifier: '',
      allow_degraded_fallback: false,
    });

    // 模型标识输入
    fireEvent.change(screen.getByTestId('manual-model-input'), {
      target: { value: 'my-model' },
    });
    expect(onChange).toHaveBeenLastCalledWith({
      mode: 'local',
      model_identifier: 'my-model',
      allow_degraded_fallback: false,
    });

    // 兜底开关
    fireEvent.click(screen.getByTestId('manual-fallback-checkbox'));
    expect(onChange).toHaveBeenLastCalledWith({
      mode: 'local',
      model_identifier: '',
      allow_degraded_fallback: true,
    });
  });

  it('TC-EBC-05 不渲染凭据字段（无服务地址 / 密钥输入位）', async () => {
    const { container } = render(
      <EmbeddingBindingControl value={INITIAL_DRAFT} onChange={vi.fn()} />,
    );

    await screen.findByTestId('embedding-binding-control');
    // 控件文本与输入占位不含凭据语义字段（INV-D10 / AC-45）
    expect(container.textContent).not.toContain('服务地址');
    expect(container.textContent).not.toContain('密钥');
    expect(container.textContent).not.toContain('api_key');
    // 全部输入位枚举：仅模型标识输入 + 兜底开关（无凭据输入位）
    const inputs = container.querySelectorAll('input');
    expect(inputs.length).toBe(2);
    expect(container.querySelectorAll('select').length).toBe(1); // 仅模式选择
  });
});
