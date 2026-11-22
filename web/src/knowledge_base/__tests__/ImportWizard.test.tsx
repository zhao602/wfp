// =============================================================================
// 导入向导测试（src/knowledge_base/__tests__/ImportWizard.test.tsx）
//
// 单文件执行：cd web && npx vitest run src/knowledge_base/__tests__/ImportWizard.test.tsx
//
// 覆盖用例（T8.10 / T8.6 CHG-27，DC-09 两段式）：
//   TC-IWZ-01 未选文件禁用下一步；选文件后可推进至策略步
//   TC-IWZ-02 策略与参数联动（fixed_length / semantic / structural）
//   TC-IWZ-03 解析预览：importDocument 调用 + 草稿分片渲染
//   TC-IWZ-04 确认导入：confirm 调用（快照版本 v1）+ onImported 回调 + 报告渲染
//   TC-IWZ-05 允许降级兜底开关透传
//   TC-IWZ-06 解析失败 → 错误条且停留在策略步
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
} from '@testing-library/react';
import { ImportWizard } from '../ImportWizard';
import { kbManagementApiClient } from '../../api/kbManagementApiClient';
import type { ImportDraftResponse } from '../kbManagementTypes';

/** 导入受理响应夹具（DC-09 第一段：草稿 + 作业 pending） */
function makeDraftResponse(): ImportDraftResponse {
  return {
    job_id: 'job-001',
    document_id: 'doc-001',
    parse_status: 'parsed',
    chunk_count: 2,
    chunks: [
      { chunk_id: 'c1', index: 0, text: '第一段内容。' },
      { chunk_id: 'c2', index: 1, text: '第二段内容。' },
    ],
    notices: ['已按段落切分'],
    strategy_effective: 'paragraph',
  };
}

/** 构造测试文件 */
function makeTxtFile(): File {
  return new File(['第一段内容。第二段内容。'], 'seed.txt', {
    type: 'text/plain',
  });
}

beforeEach(() => {
  vi.spyOn(kbManagementApiClient, 'importDocument').mockResolvedValue(
    makeDraftResponse(),
  );
  vi.spyOn(kbManagementApiClient, 'confirmImport').mockResolvedValue({
    status: 'succeeded',
    chunk_count: 2,
    upserted_count: 2,
    failed: [],
    provenance: 'hash_fallback',
    local_degraded: true,
    local_degraded_reason: '本地模型不可用，哈希降级',
    duration_ms: 12,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** 推进到步骤 2（策略步） */
async function advanceToStrategyStep(): Promise<void> {
  fireEvent.change(screen.getByTestId('wizard-file-input'), {
    target: { files: [makeTxtFile()] },
  });
  fireEvent.click(screen.getByTestId('next-step-1'));
  await screen.findByTestId('step-strategy');
}

describe('ImportWizard', () => {
  it('TC-IWZ-01 未选文件禁用下一步；选文件后可推进至策略步', async () => {
    render(<ImportWizard knowledgeBaseId="kb-1" />);

    expect(
      (screen.getByTestId('next-step-1') as HTMLButtonElement).disabled,
    ).toBe(true);

    fireEvent.change(screen.getByTestId('wizard-file-input'), {
      target: { files: [makeTxtFile()] },
    });
    expect(
      (screen.getByTestId('next-step-1') as HTMLButtonElement).disabled,
    ).toBe(false);

    fireEvent.click(screen.getByTestId('next-step-1'));
    expect(await screen.findByTestId('step-strategy')).toBeInTheDocument();
  });

  it('TC-IWZ-02 策略与参数联动（fixed_length / semantic / structural）', async () => {
    render(<ImportWizard knowledgeBaseId="kb-1" />);
    await advanceToStrategyStep();

    // 默认段落策略：不定长参数区、无语义提示
    expect(screen.queryByTestId('strategy-param-length')).toBeNull();
    expect(screen.queryByTestId('semantic-hint')).toBeNull();

    // 定长：呈现 max_chunk_length + overlap
    fireEvent.change(screen.getByTestId('strategy-select'), {
      target: { value: 'fixed_length' },
    });
    expect(screen.getByTestId('strategy-param-length')).toBeInTheDocument();
    expect(screen.getByTestId('max-chunk-length-input')).toBeInTheDocument();
    expect(screen.getByTestId('overlap-input')).toBeInTheDocument();

    // 语义：提示需 embedding 来源 + 相似度阈值
    fireEvent.change(screen.getByTestId('strategy-select'), {
      target: { value: 'semantic' },
    });
    expect(screen.getByTestId('semantic-hint')).toHaveTextContent(
      '语义分块需知识库绑定 embedding 来源',
    );
    expect(
      screen.getByTestId('similarity-threshold-input'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('strategy-param-length')).toBeNull();

    // 结构：层级输入
    fireEvent.change(screen.getByTestId('strategy-select'), {
      target: { value: 'structural' },
    });
    expect(screen.getByTestId('hierarchy-input')).toBeInTheDocument();
  });

  it('TC-IWZ-03 解析预览：importDocument 调用 + 草稿分片渲染', async () => {
    render(<ImportWizard knowledgeBaseId="kb-1" />);
    await advanceToStrategyStep();

    fireEvent.click(screen.getByTestId('parse-preview-button'));

    expect(await screen.findByTestId('step-preview')).toBeInTheDocument();
    await waitFor(() => {
      expect(kbManagementApiClient.importDocument).toHaveBeenCalledTimes(1);
    });
    const callArgs = vi.mocked(kbManagementApiClient.importDocument).mock
      .calls[0];
    expect(callArgs[0]).toBe('kb-1');
    expect(callArgs[1].name).toBe('seed.txt');
    expect(callArgs[2]).toEqual({ strategy: 'paragraph' });
    // 草稿分片预览 + 提示
    expect(screen.getByTestId('chunk-preview-list')).toHaveTextContent(
      '第一段内容。',
    );
    expect(screen.getByTestId('step-preview')).toHaveTextContent('已按段落切分');
    expect(screen.getByTestId('step-preview')).toHaveTextContent('共 2 片');
  });

  it('TC-IWZ-04 确认导入：confirm 调用（快照版本 v1）+ onImported 回调 + 报告渲染', async () => {
    const onImported = vi.fn();
    render(<ImportWizard knowledgeBaseId="kb-1" onImported={onImported} />);
    await advanceToStrategyStep();

    fireEvent.click(screen.getByTestId('parse-preview-button'));
    await screen.findByTestId('step-preview');
    fireEvent.click(screen.getByTestId('next-step-3'));
    await screen.findByTestId('step-confirm');

    fireEvent.click(screen.getByTestId('confirm-import-button'));

    await waitFor(() => {
      expect(kbManagementApiClient.confirmImport).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(kbManagementApiClient.confirmImport).mock.calls[0]).toEqual([
      'kb-1',
      'doc-001',
      { job_id: 'job-001', snapshot_version: 'v1', allow_degraded_fallback: false },
    ]);
    // 报告渲染 + 文档回调
    const report = await screen.findByTestId('import-report');
    expect(report).toHaveTextContent('succeeded');
    expect(report).toHaveTextContent('哈希降级');
    expect(onImported).toHaveBeenCalledWith('doc-001');
  });

  it('TC-IWZ-05 允许降级兜底开关透传', async () => {
    render(<ImportWizard knowledgeBaseId="kb-1" />);
    await advanceToStrategyStep();

    fireEvent.click(screen.getByTestId('parse-preview-button'));
    await screen.findByTestId('step-preview');
    fireEvent.click(screen.getByTestId('next-step-3'));
    await screen.findByTestId('step-confirm');

    fireEvent.click(screen.getByTestId('fallback-checkbox'));
    fireEvent.click(screen.getByTestId('confirm-import-button'));

    await waitFor(() => {
      expect(kbManagementApiClient.confirmImport).toHaveBeenCalledTimes(1);
    });
    expect(
      vi.mocked(kbManagementApiClient.confirmImport).mock.calls[0][2],
    ).toEqual({
      job_id: 'job-001',
      snapshot_version: 'v1',
      allow_degraded_fallback: true,
    });
  });

  it('TC-IWZ-06 解析失败 → 错误条且停留在策略步', async () => {
    vi.spyOn(kbManagementApiClient, 'importDocument').mockRejectedValue(
      new Error('文档解析失败'),
    );

    render(<ImportWizard knowledgeBaseId="kb-1" />);
    await advanceToStrategyStep();

    fireEvent.click(screen.getByTestId('parse-preview-button'));

    const errorBar = await screen.findByTestId('wizard-error');
    expect(errorBar).toHaveTextContent('文档解析失败');
    // 停留在策略步：可重试
    expect(screen.getByTestId('step-strategy')).toBeInTheDocument();
    expect(screen.getByTestId('parse-preview-button')).toBeInTheDocument();
  });
});
