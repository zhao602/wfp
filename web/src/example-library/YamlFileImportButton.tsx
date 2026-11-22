// =============================================================================
// YAML 文件导入按钮组件（src/example-library/YamlFileImportButton.tsx）
//
// 职责：提供"导入 YAML 文件"按钮，点击触发浏览器原生文件选择对话框，
//   选中文件后经 FileReader 读取并复用 onLoadExample 解析内核导入画布。
// 设计意图：从 ExampleLibraryPanel 提取，便于在 footer 中独立放置；
//   复用 footer 既有 wfp-footer-btn 皮肤与深色令牌同源（spec 5.3.3.2）。
// =============================================================================

import { useState, useCallback, useRef } from "react";
import { useThemeMode, darkTokens } from "../theme/themeMode";
import type { ImportResult } from "../workflow-definition/yamlToCanvasImporter";

/** 导入图标：13×13 内联 SVG，下箭头入底线，stroke=currentColor 继承按钮文字色 */
const importIcon = (
  <svg
    viewBox="0 0 14 14"
    width={13}
    height={13}
    aria-hidden="true"
    focusable="false"
    stroke="currentColor"
    fill="none"
    strokeWidth={1.25}
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M7 2v7" />
    <path d="M3.5 5.5L7 9l3.5-3.5" />
    <path d="M2.5 11.5h9" />
  </svg>
);

/** YAML 文件大小上限：5MB（DC-5，防止超大文件阻塞 FileReader） */
const YAML_FILE_MAX_BYTES = 5 * 1024 * 1024;
/** 允许的 YAML 文件后缀，不区分大小写（DC-6，与 input accept 双重防护） */
const YAML_FILE_EXTENSIONS = [".yaml", ".yml"] as const;

/** YAML 文件导入按钮属性 */
interface YamlFileImportButtonProps {
  onLoadExample: (yamlText: string) => ImportResult;
}

/**
 * YAML 文件导入按钮：点击触发文件选择，选中后解析导入画布。
 * 五步流程——取消判断 → 后缀校验 → 大小校验 → FileReader 读取 → onLoadExample 分流。
 */
export default function YamlFileImportButton({
  onLoadExample,
}: YamlFileImportButtonProps) {
  const themeMode = useThemeMode();
  const isDark = themeMode === "dark";
  const [error, setError] = useState<string | null>(null);
  /** 隐藏文件输入引用：按钮触发其 click 打开文件选择对话框（DC-2） */
  const fileInputRef = useRef<HTMLInputElement>(null);

  /**
   * 本地 YAML 文件选择处理（CHG-3 / DC-1~10）：
   * 复用 onLoadExample 与本地 setError，零后端调用（INV-13）。
   */
  const handleFileChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      // 1. 用户取消选择：无文件，直接返回无副作用（NFR-2.3）
      const file = event.target.files?.[0];
      if (!file) return;
      // 2. 后缀预校验：仅接受 .yaml/.yml，大小写不敏感（NFR-3.1）
      const lowerName = file.name.toLowerCase();
      if (!YAML_FILE_EXTENSIONS.some((ext) => lowerName.endsWith(ext))) {
        setError("仅支持 .yaml/.yml 文件");
        event.target.value = "";
        return;
      }
      // 3. 大小预校验：5MB 上限，防止超大文件阻塞读取（NFR-3.2）
      if (file.size > YAML_FILE_MAX_BYTES) {
        setError("文件过大，请选择 5MB 以内的 YAML 文件");
        event.target.value = "";
        return;
      }
      // 4. FileReader 读取文本 → onLoadExample 解析分流（DC-8/DC-9）
      const reader = new FileReader();
      reader.onload = () => {
        const yamlText = reader.result as string;
        const result = onLoadExample(yamlText);
        if (result.ok) {
          setError(null);
        } else {
          setError(result.error);
        }
      };
      reader.onerror = () => {
        setError("文件读取失败，请重试");
      };
      reader.readAsText(file);
      // 5. 重置 input value，允许重复选择同一文件触发 change（DC-10）
      event.target.value = "";
    },
    [onLoadExample],
  );

  return (
    <>
      <button
        className="wfp-footer-btn"
        onClick={() => fileInputRef.current?.click()}
        title="选择本地 YAML 文件导入画布"
        style={{ display: "inline-flex", alignItems: "center", gap: "5px" }}
      >
        {importIcon}
        导入 YAML
      </button>
      <input
        ref={fileInputRef}
        type="file"
        accept=".yaml,.yml"
        onChange={handleFileChange}
        style={{ display: "none" }}
      />
      {error && (
        <span
          title={error}
          style={{
            fontSize: "12px",
            color: isDark ? darkTokens.danger : "#d32f2f",
            marginLeft: "8px",
            whiteSpace: "nowrap",
          }}
        >
          {error}
        </span>
      )}
    </>
  );
}