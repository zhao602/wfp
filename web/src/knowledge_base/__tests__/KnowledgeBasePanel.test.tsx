// =============================================================================
// 知识库面板测试（src/knowledge_base/__tests__/KnowledgeBasePanel.test.tsx）
//
// 单文件执行：cd web && npx vitest run src/knowledge_base/__tests__/KnowledgeBasePanel.test.tsx
//
// 覆盖用例（T8.10 / T8.4 CHG-25）：
//   TC-KBP-01 列表渲染（含绑定模型摘要，零凭据）
//   TC-KBP-02 空态呈现（暂无知识库 + 创建表单可用）
//   TC-KBP-03 列表加载失败 → 错误条且页面可渲染（错误降级）
//   TC-KBP-04 手工填写绑定 → 创建调用（请求体无服务地址字段）
//   TC-KBP-05 创建失败 → 错误条且表单保留
//   TC-KBP-06 候选渲染点击选择 → 创建调用携带候选标识
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from '@testing-library/react';
import { KnowledgeBasePanel } from '../KnowledgeBasePanel';
import { kbManagementApiClient } from '../../api/kbManagementApiClient';
import type {
  EmbeddingCandidatesResponse,
  KnowledgeBaseListResponse,
  KnowledgeBaseView,
} from '../kbManagementTypes';

/** 知识库视图夹具 */
function makeKb(overrides: Partial<KnowledgeBaseView> = {}): KnowledgeBaseView {
  return {
    knowledge_base_id: 'product_docs',
    name: '产品文档库',
    embedding_model: {
      mode: 'local',
      model_identifier: 'bge-small-zh',
      allow_degraded_fallback: false,
    },
    default_chunk_strategy: 'paragraph',
    document_count: 3,
    chunk_count: 42,
    created_at: '2026-10-26T00:00:00Z',
    updated_at: '2026-10-26T00:00:00Z',
    ...overrides,
  };
}

/** 两行夹具列表响应 */
function makeListResponse(): KnowledgeBaseListResponse {
  return {
    items: [
      makeKb(),
      makeKb({
        knowledge_base_id: 'sales_kb',
        name: '销售知识库',
        embedding_model: {
          mode: 'api',
          model_identifier: 'text-embedding-3-small',
          allow_degraded_fallback: true,
        },
        default_chunk_strategy: undefined,
        document_count: 0,
        chunk_count: 0,
      }),
    ],
    total: 2,
  };
}

/** 空候选响应（settings 无 Embedding 字段 → 恒空候选 + 允许手工） */
function makeEmptyCandidates(): EmbeddingCandidatesResponse {
  return { candidates: [], total: 0, allow_manual_input: true };
}

beforeEach(() => {
  vi.spyOn(kbManagementApiClient, 'listKnowledgeBases').mockResolvedValue(
    makeListResponse(),
  );
  vi.spyOn(kbManagementApiClient, 'listEmbeddingModels').mockResolvedValue(
    makeEmptyCandidates(),
  );
  vi.spyOn(kbManagementApiClient, 'createKnowledgeBase').mockResolvedValue(
    makeKb(),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('KnowledgeBasePanel', () => {
  it('TC-KBP-01 列表渲染（含绑定模型摘要，零凭据）', async () => {
    const { container } = render(<KnowledgeBasePanel />);

    await screen.findByTestId('kb-row-product_docs');
    expect(screen.getByTestId('kb-row-sales_kb')).toBeInTheDocument();
    // 标识 / 名称 / 绑定摘要（模式徽章 + 模型标识）/ 计数
    expect(container).toHaveTextContent('product_docs');
    expect(container).toHaveTextContent('产品文档库');
    expect(container).toHaveTextContent('bge-small-zh');
    expect(container).toHaveTextContent('text-embedding-3-small');
    expect(container).toHaveTextContent('42');
    // 零凭据：列表不渲染服务地址字段
    expect(container.textContent).not.toContain('服务地址');
  });

  it('TC-KBP-02 空态呈现（暂无知识库 + 创建表单可用）', async () => {
    vi.spyOn(kbManagementApiClient, 'listKnowledgeBases').mockResolvedValue({
      items: [],
      total: 0,
    });

    render(<KnowledgeBasePanel />);

    const empty = await screen.findByTestId('kb-empty');
    expect(empty).toHaveTextContent('暂无知识库');
    expect(screen.getByTestId('create-form')).toBeInTheDocument();
    expect(screen.getByTestId('kb-id-input')).toBeInTheDocument();
  });

  it('TC-KBP-03 列表加载失败 → 错误条且页面可渲染（错误降级）', async () => {
    vi.spyOn(kbManagementApiClient, 'listKnowledgeBases').mockRejectedValue(
      new Error('知识库列表加载失败'),
    );

    render(<KnowledgeBasePanel />);

    const errorBar = await screen.findByTestId('kb-panel-error');
    expect(errorBar).toHaveTextContent('知识库列表加载失败');
    // 页面仍可渲染：表格与创建表单在场
    expect(screen.getByTestId('kb-list-table')).toBeInTheDocument();
    expect(screen.getByTestId('create-form')).toBeInTheDocument();
  });

  it('TC-KBP-04 手工填写绑定 → 创建调用（请求体无服务地址字段）', async () => {
    render(<KnowledgeBasePanel />);
    await screen.findByTestId('kb-list-table');

    fireEvent.change(screen.getByTestId('kb-id-input'), {
      target: { value: 'demo_kb' },
    });
    fireEvent.change(screen.getByTestId('kb-name-input'), {
      target: { value: '演示知识库' },
    });
    // 空候选 → 手工填写模型标识
    fireEvent.change(screen.getByTestId('manual-model-input'), {
      target: { value: 'demo-model' },
    });
    fireEvent.click(screen.getByTestId('create-submit-button'));

    await waitFor(() => {
      expect(kbManagementApiClient.createKnowledgeBase).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(
      kbManagementApiClient.createKnowledgeBase,
    ).mock.calls[0][0];
    expect(payload.knowledge_base_id).toBe('demo_kb');
    expect(payload.name).toBe('演示知识库');
    expect(payload.embedding_model).toEqual({
      mode: 'local',
      model_identifier: 'demo-model',
      allow_degraded_fallback: false,
    });
    // 零凭据：请求绑定不携带服务地址字段（INV-D10）
    expect(payload.embedding_model).not.toHaveProperty('base_url');
    // 创建成功后列表重载
    await waitFor(() => {
      expect(kbManagementApiClient.listKnowledgeBases).toHaveBeenCalledTimes(2);
    });
  });

  it('TC-KBP-05 创建失败 → 错误条且表单保留', async () => {
    vi.spyOn(kbManagementApiClient, 'createKnowledgeBase').mockRejectedValue(
      new Error('标识已占用'),
    );

    render(<KnowledgeBasePanel />);
    await screen.findByTestId('kb-list-table');

    fireEvent.change(screen.getByTestId('kb-id-input'), {
      target: { value: 'dup_kb' },
    });
    fireEvent.change(screen.getByTestId('kb-name-input'), {
      target: { value: '重复' },
    });
    fireEvent.change(screen.getByTestId('manual-model-input'), {
      target: { value: 'demo-model' },
    });
    fireEvent.click(screen.getByTestId('create-submit-button'));

    const errorBar = await screen.findByTestId('create-error');
    expect(errorBar).toHaveTextContent('标识已占用');
    // 表单内容保留（不因失败清空）
    expect(
      (screen.getByTestId('kb-id-input') as HTMLInputElement).value,
    ).toBe('dup_kb');
  });

  it('TC-KBP-06 候选渲染点击选择 → 创建调用携带候选标识', async () => {
    vi.spyOn(kbManagementApiClient, 'listEmbeddingModels').mockResolvedValue({
      candidates: [
        { model_identifier: 'bge-small-zh', mode: 'local', label: 'BGE 小模型' },
      ],
      total: 1,
      allow_manual_input: true,
    });

    render(<KnowledgeBasePanel />);
    await screen.findByTestId('embedding-candidates');

    fireEvent.click(screen.getByTestId('embedding-candidate-bge-small-zh'));
    fireEvent.change(screen.getByTestId('kb-id-input'), {
      target: { value: 'cand_kb' },
    });
    fireEvent.change(screen.getByTestId('kb-name-input'), {
      target: { value: '候选库' },
    });
    fireEvent.click(screen.getByTestId('create-submit-button'));

    await waitFor(() => {
      expect(kbManagementApiClient.createKnowledgeBase).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(
      kbManagementApiClient.createKnowledgeBase,
    ).mock.calls[0][0];
    expect(payload.embedding_model.model_identifier).toBe('bge-small-zh');
    expect(payload.embedding_model.mode).toBe('local');
  });
});
