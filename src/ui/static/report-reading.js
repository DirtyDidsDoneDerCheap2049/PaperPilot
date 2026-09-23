/* Reports are read as chapters. Internal JSON is not part of the user-facing reader. */
function reportReadingHTML(markdown){
  let source=String(markdown||'');
  const oldTitle=source.match(/^# (.+)$/m)?.[1]||'';
  const legacy=oldTitle!=='研究方向分析'&&/^## 0\./m.test(source);
  if(legacy)source=source.replace(/^# .+$/m,'# 历史研究报告');
  const sections=source.split(/(?=^## )/m),main=[],appendices=[];
  let inAppendix=false;
  for(let section of sections){
    const heading=section.split('\n')[0].replace(/^##\s*/,''),original=/^附录[：:]/.test(heading);
    if(original)continue; // Preserve source files, hide raw direction/matrix payloads in the reader.
    if(heading==='材料与审计附录'){inAppendix=true;continue;}
    const technical=inAppendix||/^(?:2\.|3\.|6\.|8\.)/.test(heading);
    // Historical repr-style metadata can be incomplete; don't present it as prose.
    const rawLines=[];
    section=section.split('\n').filter(line=>{if(/\{['"][a-z_]+['"]\s*:/.test(line)){rawLines.push(line);return false;}return true;}).join('\n');
    section=section.replace(/```(?:json)?\s*\n[\s\S]*?```/g,'');
    const html=safeMarkdown(section);
    if(technical)appendices.push(`<details class="report-appendix"><summary>${esc(heading)}</summary><div>${safeMarkdown(section.replace(/^##[^\n]*\n/,''))}</div></details>`);
    else main.push(`<section class="report-chapter">${html}${rawLines.length?'<p class="report-legacy-note">这份历史报告含旧版结构化记录；正文仅展示可读段落，原始文件保持不变。</p>':''}</section>`);
  }
  return `<article class="report-reading">${legacy?`<p class="report-legacy-note">历史结果：保留原结论与统计，本次仅调整阅读格式，未重新验证。</p><details class="report-history-title"><summary>原报告标题</summary><p>${esc(oldTitle)}</p></details>`:''}${main.join('')}${appendices.length?'<section class="report-appendices"><h2>材料与审计记录</h2><p>按需展开，核对论文来源、处理状态和证据。</p>'+appendices.join('')+'</section>':''}</article>`;
}
