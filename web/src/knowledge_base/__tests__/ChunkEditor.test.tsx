// =============================================================================
// 分片编辑器测试（src/knowledge_base/__tests__/ChunkEditor.test.tsx）
//
// 单文件执行：cd web && npx vitest run src/knowledge_base/__tests__/ChunkEditor.test.tsx
//
// 覆盖用例（T8.10 / T8.7 CHG-28，AC-30~AC-35 / AC-38 / INV-D19）：
//   TC-CHK-01 分片列表渲染 + 分页下一页（服务端分页）
//   TC-CHK-02 首页上一页禁用
//   TC-CHK-03 合并相邻校验：非相邻选择拦截且不发起草稿请求
//   TC-CHK-04 合并相邻成功：merge 指令 + 草稿横幅（未确认不影响检索）
//   TC-CHK-05 拆分点校验：区间 + 严格递增；合法拆分发起 split 指令
//   TC-CHK-06 编辑校验：非空 + 超长拦截；合法编辑发起 edit 指令
//   TC-CHK-07 草稿确认：confirm 调用 + 结果渲染 + 列表重载
//   TC-CHK-08 版本冲突 → 冲突提示（DRAFT_VERSION_CONFLICT）
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from '@testing-library/react';
import { ChunkEditor } from '../ChunkEditor';
import {
  kbManagementApiClient,
  KbApiException,
} from '../../api/kbManagementApiClient';
import type {
  ChunkDraftConfirmResponse,
  ChunkDraftResponse,
  ChunkListResponse,
  ChunkView,
} from '../kbManagementTypes';

/** 分片视图夹具（chunk-a 文本 18 字符，供拆分点区间校验） */
function makeChunks(): ChunkView[] {
  return [
    {
      chunk_id: 'chunk-a',
      index: 0,
      text: '一二三四五六七八九十甲乙丙丁戊己庚壬',
      vectorized: true,
      provenance: 'hash_fallback',
      local_degraded: false,
    },
    {
      chunk_id: 'chunk-b',
      index: 1,
      text: '第二段内容，用于相邻合并。',
      vectorized: true,
      provenance: 'hash_fallback',
      local_degraded: false,
    },
    {
      chunk_id: 'chunk-c',
      index: 2,
      text: '第三段内容，不相邻合并拦截。',
      vectorized: false,
      local_degraded: false,
    },
  ];
}

/** 分片列表响应夹具（25 片 / 每页 20 → 2 页） */
function makeChunkList(page: number): ChunkListResponse {
  return {
    items: makeChunks(),
    total: 25,
    page,
    page_size: 20,
    vectorized_count: 2,
    degraded_count: 0,
  };
}

/** 调整草稿响应夹具 */
function makeDraftResponse(): ChunkDraftResponse {
  return {
    draft_version: 'v2',
    chunks: [
      { chunk_id: 'chunk-merged', index: 0, text: '一二三四五六七八九十甲乙丙丁戊己庚壬第二段内容，用于相邻合并。' },
      { chunk_id: 'chunk-c', index: 1, text: '第三段内容，不相邻合并拦截。' },
    ],
    notices: ['已合并相邻分片'],
  };
}

/** 草稿确认响应夹具 */
function makeConfirmResponse(): ChunkDraftConfirmResponse {
  return {
    status: 'succeeded',
    rewritten: ['chunk-merged'],
    deleted: [],
    local_degraded: false,
  };
}

beforeEach(() => {
  vi.spyOn(kbManagementApiClient, 'listChunks').mockImplementation(
    (_kb: string, _doc: string, page?: number) =>
      Promise.resolve(makeChunkList(page ?? 1)),
  );
  vi.spyOn(kbManagementApiClient, 'applyChunkDraft').mockResolvedValue(
    makeDraftResponse(),
  );
  vi.spyOn(kbManagementApiClient, 'confirmChunkDraft').mockResolvedValue(
    makeConfirmResponse(),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ChunkEditor', () => {
  it('TC-CHK-01 分片列表渲染 + 分页下一页（服务端分页）', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);

    await screen.findByTestId('chunk-row-chunk-a');
    expect(screen.getByTestId('chunk-list-table')).toHaveTextContent('已向量化');
    expect(screen.getByTestId('page-indicator')).toHaveTextContent('第 1 / 2 页');

    fireEvent.click(screen.getByTestId('next-page-button'));

    await waitFor(() => {
      expect(kbManagementApiClient.listChunks).toHaveBeenCalledTimes(2);
    });
    expect(
      vi.mocked(kbManagementApiClient.listChunks).mock.calls[1],
    ).toEqual(['kb-1', 'doc-1', 2, 20, true]);
    expect(await screen.findByTestId('page-indicator')).toHaveTextContent(
      '第 2 / 2 页',
    );
  });

  it('TC-CHK-02 首页上一页禁用', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);

    await screen.findByTestId('chunk-list-table');
    expect(
      (screen.getByTestId('prev-page-button') as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it('TC-CHK-03 合并相邻校验：非相邻选择拦截且不发起草稿请求', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);
    await screen.findByTestId('chunk-list-table');

    // 选择索引 0 与 2（跳过 1，非相邻）
    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-a'));
    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-c'));
    fireEvent.click(screen.getByTestId('merge-button'));

    expect(await screen.findByTestId('merge-error')).toHaveTextContent(
      '仅允许合并相邻分片',
    );
    expect(kbManagementApiClient.applyChunkDraft).not.toHaveBeenCalled();
  });

  it('TC-CHK-04 合并相邻成功：merge 指令 + 草稿横幅（未确认不影响检索）', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);
    await screen.findByTestId('chunk-list-table');

    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-a'));
    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-b'));
    fireEvent.click(screen.getByTestId('merge-button'));

    await screen.findByTestId('chunk-draft-panel');
    expect(kbManagementApiClient.applyChunkDraft).toHaveBeenCalledWith(
      'kb-1',
      'doc-1',
      {
        base_version: 'v1',
        operations: [{ op: 'merge', chunk_ids: ['chunk-a', 'chunk-b'] }],
      },
    );
    // INV-D19 界面明示：草稿未确认不影响检索
    expect(screen.getByTestId('draft-banner')).toHaveTextContent(
      '草稿未确认不影响检索',
    );
  });

  it('TC-CHK-05 拆分点校验：区间 + 严格递增；合法拆分发起 split 指令', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);
    await screen.findByTestId('chunk-list-table');

    fireEvent.change(screen.getByTestId('split-target-select'), {
      target: { value: 'chunk-a' },
    });

    // 区间外（文本 18 字符）
    fireEvent.change(screen.getByTestId('split-points-input'), {
      target: { value: '25' },
    });
    fireEvent.click(screen.getByTestId('split-button'));
    expect(await screen.findByTestId('split-error')).toHaveTextContent('区间');

    // 非严格递增
    fireEvent.change(screen.getByTestId('split-points-input'), {
      target: { value: '5,3' },
    });
    fireEvent.click(screen.getByTestId('split-button'));
    expect(await screen.findByTestId('split-error')).toHaveTextContent(
      '严格递增',
    );
    expect(kbManagementApiClient.applyChunkDraft).not.toHaveBeenCalled();

    // 合法拆分
    fireEvent.change(screen.getByTestId('split-points-input'), {
      target: { value: '5,10' },
    });
    fireEvent.click(screen.getByTestId('split-button'));

    await screen.findByTestId('chunk-draft-panel');
    expect(kbManagementApiClient.applyChunkDraft).toHaveBeenCalledWith(
      'kb-1',
      'doc-1',
      {
        base_version: 'v1',
        operations: [{ op: 'split', chunk_id: 'chunk-a', split_points: [5, 10] }],
      },
    );
  });

  it('TC-CHK-06 编辑校验：非空 + 超长拦截；合法编辑发起 edit 指令', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);
    await screen.findByTestId('chunk-list-table');

    fireEvent.change(screen.getByTestId('edit-target-select'), {
      target: { value: 'chunk-a' },
    });
    // 选择目标后文本自动回填（长度 18）
    expect(
      (screen.getByTestId('edit-textarea') as HTMLTextAreaElement).value,
    ).toHaveLength(18);

    // 非空拦截
    fireEvent.change(screen.getByTestId('edit-textarea'), {
      target: { value: '   ' },
    });
    fireEvent.click(screen.getByTestId('edit-button'));
    expect(await screen.findByTestId('edit-error')).toHaveTextContent(
      '不能为空',
    );

    // 超长拦截（> 2000 字符）
    fireEvent.change(screen.getByTestId('edit-textarea'), {
      target: { value: '长'.repeat(2001) },
    });
    fireEvent.click(screen.getByTestId('edit-button'));
    expect(await screen.findByTestId('edit-error')).toHaveTextContent('2000');
    expect(kbManagementApiClient.applyChunkDraft).not.toHaveBeenCalled();

    // 合法编辑
    fireEvent.change(screen.getByTestId('edit-textarea'), {
      target: { value: '编辑后的合法文本。' },
    });
    fireEvent.click(screen.getByTestId('edit-button'));

    await screen.findByTestId('chunk-draft-panel');
    expect(kbManagementApiClient.applyChunkDraft).toHaveBeenCalledWith(
      'kb-1',
      'doc-1',
      {
        base_version: 'v1',
        operations: [
          { op: 'edit', chunk_id: 'chunk-a', text: '编辑后的合法文本。' },
        ],
      },
    );
  });

  it('TC-CHK-07 草稿确认：confirm 调用 + 结果渲染 + 列表重载', async () => {
    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);
    await screen.findByTestId('chunk-list-table');

    // 生成草稿（合并相邻）
    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-a'));
    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-b'));
    fireEvent.click(screen.getByTestId('merge-button'));
    await screen.findByTestId('chunk-draft-panel');

    // 确认草稿（触发向量重算）
    fireEvent.click(screen.getByTestId('confirm-draft-button'));

    await waitFor(() => {
      expect(kbManagementApiClient.confirmChunkDraft).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(kbManagementApiClient.confirmChunkDraft).mock.calls[0]).toEqual([
      'kb-1',
      'doc-1',
      { draft_version: 'v2' },
    ]);
    // 确认结果渲染 + 列表重载
    const result = await screen.findByTestId('draft-result');
    expect(result).toHaveTextContent('succeeded');
    expect(result).toHaveTextContent('重算 1 片');
    await waitFor(() => {
      expect(kbManagementApiClient.listChunks).toHaveBeenCalledTimes(2);
    });
  });

  it('TC-CHK-08 版本冲突 → 冲突提示（DRAFT_VERSION_CONFLICT）', async () => {
    vi.spyOn(kbManagementApiClient, 'confirmChunkDraft').mockRejectedValue(
      new KbApiException(
        'DRAFT_VERSION_CONFLICT',
        '草稿版本冲突：请求 v2，当前 v3',
        {},
        409,
      ),
    );

    render(<ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />);
    await screen.findByTestId('chunk-list-table');

    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-a'));
    fireEvent.click(screen.getByTestId('chunk-checkbox-chunk-b'));
    fireEvent.click(screen.getByTestId('merge-button'));
    await screen.findByTestId('chunk-draft-panel');

    fireEvent.click(screen.getByTestId('confirm-draft-button'));

    const conflict = await screen.findByTestId('conflict-message');
    expect(conflict).toHaveTextContent('草稿版本冲突');
    // 草稿面板仍在场（可放弃或刷新重试）
    expect(screen.getByTestId('chunk-draft-panel')).toBeInTheDocument();
  });
});
