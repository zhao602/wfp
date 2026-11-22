// =============================================================================
// 双主题测试（src/knowledge_base/__tests__/theme.test.tsx）
//
// 单文件执行：cd web && npx vitest run src/knowledge_base/__tests__/theme.test.tsx
//
// 覆盖用例（T8.10，DFX 4.4.4 深浅双轨）：
//   TC-THM-01 浅色：wfp-kbm-* 类族 + 浅色取值，无深色背景令牌
//   TC-THM-02 深色：darkTokens 同源注入（surface.bg / text.primary /
//                   surface.divider / danger 编译期强绑定）
//   TC-THM-03 深浅双轨差异：同组件两种主题下注入样式不同
//   TC-THM-04 ChunkEditor 深色令牌同源
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { KnowledgeBasePanel } from '../KnowledgeBasePanel';
import { ChunkEditor } from '../ChunkEditor';
import { kbManagementApiClient } from '../../api/kbManagementApiClient';
import { ThemeContext, darkTokens } from '../../theme/themeMode';

/** 收集容器内全部 <style> 注入内容 */
function collectStyleText(container: HTMLElement): string {
  const styles = container.querySelectorAll('style');
  const parts: string[] = [];
  styles.forEach((style) => {
    parts.push(style.textContent ?? '');
  });
  return parts.join('\n');
}

beforeEach(() => {
  vi.spyOn(kbManagementApiClient, 'listKnowledgeBases').mockResolvedValue({
    items: [
      {
        knowledge_base_id: 'product_docs',
        name: '产品文档库',
        embedding_model: {
          mode: 'local',
          model_identifier: 'bge-small-zh',
          allow_degraded_fallback: false,
        },
        default_chunk_strategy: 'paragraph',
        document_count: 1,
        chunk_count: 8,
      },
    ],
    total: 1,
  });
  vi.spyOn(kbManagementApiClient, 'listEmbeddingModels').mockResolvedValue({
    candidates: [],
    total: 0,
    allow_manual_input: true,
  });
  vi.spyOn(kbManagementApiClient, 'listChunks').mockResolvedValue({
    items: [
      {
        chunk_id: 'chunk-a',
        index: 0,
        text: '第一段内容。',
        vectorized: true,
        local_degraded: false,
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
    vectorized_count: 1,
    degraded_count: 0,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('向量知识库管理双主题（wfp-kbm-* + darkTokens）', () => {
  it('TC-THM-01 浅色：wfp-kbm-* 类族 + 浅色取值，无深色背景令牌', async () => {
    const { container } = render(<KnowledgeBasePanel />);

    await screen.findByTestId('kb-row-product_docs');

    // wfp-kbm-* 类族在场
    expect(container.querySelector('.wfp-kbm-panel')).not.toBeNull();
    expect(container.querySelector('.wfp-kbm-table')).not.toBeNull();
    expect(container.querySelector('.wfp-kbm-title')).not.toBeNull();

    const styleText = collectStyleText(container);
    // 浅色取值：面板白底 + 深色文字
    expect(styleText).toContain('#ffffff');
    expect(styleText).toContain('#1a1a1a');
    // 无深色背景令牌混入（浅色轨纯净）
    expect(styleText).not.toContain(darkTokens.surface.bg);
  });

  it('TC-THM-02 深色：darkTokens 同源注入（编译期强绑定）', async () => {
    const { container } = render(
      <ThemeContext.Provider value="dark">
        <KnowledgeBasePanel />
      </ThemeContext.Provider>,
    );

    await screen.findByTestId('kb-row-product_docs');

    const styleText = collectStyleText(container);
    // 深色令牌同源断言：组件注入值与 darkTokens 总表一致（杜绝深色数值漂移）
    expect(styleText).toContain(darkTokens.surface.bg);
    expect(styleText).toContain(darkTokens.text.primary);
    expect(styleText).toContain(darkTokens.surface.divider);
    expect(styleText).toContain(darkTokens.danger);
  });

  it('TC-THM-03 深浅双轨差异：同组件两种主题下注入样式不同', async () => {
    const lightResult = render(<KnowledgeBasePanel />);
    await screen.findByTestId('kb-row-product_docs');
    const lightStyle = collectStyleText(lightResult.container);
    const lightTable = lightResult.container.querySelector('.wfp-kbm-table');
    expect(lightTable).not.toBeNull();
    cleanup();

    const darkResult = render(
      <ThemeContext.Provider value="dark">
        <KnowledgeBasePanel />
      </ThemeContext.Provider>,
    );
    await screen.findByTestId('kb-row-product_docs');
    const darkStyle = collectStyleText(darkResult.container);

    // 双轨差异：深色轨注入深色令牌，与浅色轨样式内容不同
    expect(darkStyle).not.toBe(lightStyle);
    expect(darkStyle).toContain(darkTokens.surface.bg);
    expect(lightStyle).not.toContain(darkTokens.surface.bg);
  });

  it('TC-THM-04 ChunkEditor 深色令牌同源', async () => {
    const { container } = render(
      <ThemeContext.Provider value="dark">
        <ChunkEditor knowledgeBaseId="kb-1" documentId="doc-1" />
      </ThemeContext.Provider>,
    );

    await screen.findByTestId('chunk-editor');

    const styleText = collectStyleText(container);
    expect(styleText).toContain(darkTokens.surface.bg);
    expect(styleText).toContain(darkTokens.text.primary);
    expect(styleText).toContain(darkTokens.surface.divider);
    // 类族在场
    expect(container.querySelector('.wfp-kbm-editor')).not.toBeNull();
    expect(container.querySelector('.wfp-kbm-table')).not.toBeNull();
  });
});
