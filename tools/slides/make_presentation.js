const pptxgen = require('pptxgenjs');
const path = require('path');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = '数据治理课程 第一组';
pptx.subject = '实验03 数据对象与大模型多智能体基础';
pptx.title = '基于 LangGraph 和 MEDS 的医疗 EMR 数据治理';
pptx.company = '数据治理课程';
pptx.lang = 'zh-CN';
pptx.theme = {
  headFontFace: 'Microsoft YaHei', bodyFontFace: 'Microsoft YaHei', lang: 'zh-CN'
};

const W = 13.333, H = 7.5;
const C = {
  ink: '183246', deep: '10354A', teal: '078F87', mint: 'DDF4EF',
  coral: 'D45C4B', paleCoral: 'FBE9E5', gold: 'C8903D', paleGold: 'FFF3DD',
  bg: 'F7F9FA', white: 'FFFFFF', line: 'D7E1E5', muted: '637680',
  paleBlue: 'E9F1F4', near: 'ECF4F2'
};
const ST = pptx.ShapeType;
const A = path.join(__dirname, 'PPT制作交接包', '素材');
const images = {
  run: path.join(A, '01_LangGraph与MEDS校验成功.png'),
  issues: path.join(A, '02_质量问题明细.png'),
  audit: path.join(A, '03_Agent审计链路.png'),
  events: path.join(A, '04_MEDS事件输出.png')
};

function shape(s, type, x, y, w, h, fill, line = fill, extra = {}) {
  s.addShape(type, {x,y,w,h,rectRadius:0,fill:{color:fill},line:{color:line,width:1},...extra});
}
function txt(s, text, x, y, w, h, opts={}) {
  s.addText(text, {x,y,w,h,fontFace:'Microsoft YaHei',fontSize:18,
    color:C.ink,margin:0,breakLine:false,valign:'mid',fit:'shrink',
    ...opts});
}
function line(s,x1,y1,x2,y2,color=C.line,width=1.5,dash) {
  s.addShape(ST.line,{x:x1,y:y1,w:x2-x1,h:y2-y1,
    line:{color,width,dashType:dash,beginArrowType:'none',endArrowType:'none'}});
}
function arrow(s,x1,y1,x2,y2,color=C.teal,width=2) {
  s.addShape(ST.line,{x:x1,y:y1,w:x2-x1,h:y2-y1,
    line:{color,width,beginArrowType:'none',endArrowType:'triangle'}});
}
function base(num,section,title,subtitle='') {
  const s=pptx.addSlide(); s.background={color:C.bg};
  shape(s,ST.rect,0,0,.12,H,C.teal);
  txt(s,section.toUpperCase(),.65,.34,7,.24,{fontSize:10,bold:true,color:C.teal,charSpacing:1.2});
  txt(s,title,.65,.77,12.05,.55,{fontSize:29,bold:true,color:C.deep});
  if(subtitle) txt(s,subtitle,.67,1.42,12,.36,{fontSize:13,color:C.muted});
  line(s,.65,7.11,12.68,7.11,C.line,1);
  txt(s,'实验03  |  数据对象与大模型多智能体基础',.67,7.16,8,.2,{fontSize:9,color:C.muted});
  txt(s,String(num).padStart(2,'0'),12.15,7.14,.5,.24,{fontSize:10,bold:true,color:C.teal,align:'right'});
  return s;
}
function pill(s,text,x,y,w,fill=C.mint,color=C.teal) {
  shape(s,ST.roundRect,x,y,w,.36,fill,fill,{radius:.08});
  txt(s,text,x+.1,y+.02,w-.2,.31,{fontSize:11,bold:true,color,align:'center'});
}
function box(s,x,y,w,h,fill=C.white,lineColor=C.line) {
  shape(s,ST.rect,x,y,w,h,fill,lineColor);
}
function bullet(s,top,text,number,accent=C.teal) {
  shape(s,ST.ellipse,.75,top+.08,.34,.34,accent,accent);
  txt(s,String(number),.75,top+.08,.34,.34,{fontSize:12,color:C.white,bold:true,align:'center'});
  txt(s,text,1.25,top,10.9,.52,{fontSize:18,color:C.ink});
}
function caption(s,text,x,y,w) { txt(s,text,x,y,w,.23,{fontSize:10,color:C.muted}); }
function miniTable(s,rows,x,y,w,colW,rowH,headFill=C.deep,fontSize=13) {
  let cy=y;
  rows.forEach((row,i)=>{
    const bg=i===0?headFill:(i%2?C.white:'F0F5F6');
    box(s,x,cy,w,rowH,bg,i===0?headFill:C.line);
    let cx=x;
    row.forEach((v,j)=>{
      if(j) line(s,cx,cy,cx,cy+rowH,i===0?'547181':C.line,.6);
      txt(s,String(v),cx+.11,cy+.04,colW[j]-.22,rowH-.08,{fontSize,
        bold:i===0,color:i===0?C.white:C.ink,valign:'mid'});
      cx+=colW[j];
    });
    cy+=rowH;
  });
}

// 01 Cover
{
  const s=pptx.addSlide(); s.background={color:C.deep};
  shape(s,ST.rect,0,0,.14,H,C.teal);
  txt(s,'实验03  ·  数据治理',.78,.62,5,.28,{fontSize:15,bold:true,color:'86D8CB'});
  txt(s,'基于 LangGraph 和 MEDS 的\n医疗 EMR 数据治理',.78,1.3,10.8,1.55,
    {fontSize:35,bold:true,color:C.white,breakLine:false,valign:'top'});
  txt(s,'从六张模拟源表到可追溯的患者纵向事件',.82,3.09,10,.46,
    {fontSize:20,color:'D7E9ED'});
  const labels=['6 张源表','6 个 Agent','17 条事件'];
  labels.forEach((v,i)=>{
    const x=.83+i*4.03;
    box(s,x,4.48,3.62,1.05,'19445A','315D6F');
    txt(s,v,x+.2,4.7,3.2,.54,{fontSize:22,bold:true,color:C.white});
    if(i<2) arrow(s,x+3.68,5,x+3.99,5,'77C7BC',2.2);
  });
  line(s,.83,6.87,12.45,6.87,'456577',1);
  txt(s,'课程：数据治理   |   实验03   |   2026.10',.83,6.96,9,.22,
    {fontSize:11,color:'B8D2D9'});
  txt(s,'01',12.12,6.94,.33,.25,{fontSize:10,color:'B8D2D9',align:'right'});
}

// 02 Scenario
{
  const s=base(2,'01 / 场景','从多中心本地 EMR 到标准事件数据',
    '两个模拟医院节点各自治理本地记录，原始病历不集中共享。');
  ['HOSP_A','HOSP_B'].forEach((site,i)=>{
    const x=.82+i*4.45;
    box(s,x,2.2,3.55,2.38,C.white,C.line);
    shape(s,ST.rect,x,2.2,3.55,.09,i?C.gold:C.teal);
    txt(s,site,x+.27,2.55,3,.39,{fontSize:21,bold:true,color:C.deep});
    txt(s,'本地 EMR',x+.27,3.08,2.8,.31,{fontSize:15,color:C.muted});
    txt(s,'患者 · 就诊 · 诊断\n用药 · 检验 · 临床文本',x+.27,3.55,3,.7,
      {fontSize:15,color:C.ink,breakLine:false,valign:'top'});
  });
  line(s,2.6,4.59,2.6,5.02,C.teal,1.8);
  line(s,7.05,4.59,7.05,5.02,C.teal,1.8);
  line(s,2.6,5.02,9.46,5.02,C.teal,1.8);
  line(s,7.05,5.02,9.46,5.02,C.teal,1.8);
  line(s,9.46,3.35,9.46,5.02,C.teal,1.8);
  arrow(s,9.46,3.35,9.96,3.35,C.teal,2);
  box(s,10.03,2.47,2.35,1.74,C.mint,C.teal);
  txt(s,'MEDS\n事件数据',10.29,2.77,1.84,1.09,
    {fontSize:22,bold:true,color:C.deep,align:'center',breakLine:false});
  pill(s,'原始病历不出节点',.83,5.72,3.22,C.paleCoral,C.coral);
  txt(s,'本实验目标：对象化 → 关联 → 标准映射 → 校验与发布',4.34,5.65,7.88,.6,
    {fontSize:18,bold:true,color:C.ink});
}

// 03 Challenge
{
  const s=base(3,'02 / 问题','多表 EMR 为什么不能直接用于研究？',
    '同一患者的诊疗轨迹横跨多张表，记录的连接和可信度都需要治理。');
  const items=[['患者','标识'],['就诊','时间'],['诊断','编码'],['用药','映射'],['检验','数值'],['文本','缺失']];
  items.forEach(([a,b],i)=>{
    const col=i%3,row=Math.floor(i/3),x=.82+col*4.2,y=2.05+row*1.56;
    box(s,x,y,3.73,1.13,C.white,C.line);
    shape(s,ST.rect,x,y,.1,1.13,[C.teal,C.gold,C.coral][col]);
    txt(s,a,x+.26,y+.21,1.48,.38,{fontSize:20,bold:true});
    txt(s,b,x+2.06,y+.28,1.28,.28,{fontSize:14,color:C.muted,align:'right'});
  });
  box(s,.82,5.45,11.7,.83,C.deep,C.deep);
  txt(s,'关联错误  ·  缺失时间  ·  本地编码  ·  重复与异常值',1.11,5.67,11.1,.4,
    {fontSize:20,bold:true,color:C.white,align:'center'});
}

// 04 Objects
{
  const s=base(4,'03 / 对象','先建立对象，再生成事件',
    '关联 Agent 同时核验 patient_id 与 encounter_id，明确每条记录的归属。');
  const nodes=[
    {x:.85,y:2.68,w:2.7,h:1.5,t:'Patient',d:'patient_id → subject_id',c:C.teal},
    {x:5.27,y:2.68,w:2.7,h:1.5,t:'Encounter',d:'encounter_id',c:C.deep},
  ];
  nodes.forEach(n=>{
    box(s,n.x,n.y,n.w,n.h,C.white,n.c);
    shape(s,ST.rect,n.x,n.y,n.w,.1,n.c);
    txt(s,n.t,n.x+.18,n.y+.35,n.w-.36,.4,{fontSize:21,bold:true,color:n.c,align:'center'});
    txt(s,n.d,n.x+.12,n.y+.93,n.w-.24,.27,{fontSize:12,color:C.muted,align:'center'});
  });
  arrow(s,3.57,3.42,5.14,3.42,C.teal,2.2);
  txt(s,'1 : N',4.06,3.04,.73,.25,{fontSize:12,bold:true,color:C.teal,align:'center'});
  arrow(s,8.01,3.42,9.03,3.42,C.teal,2.2);
  txt(s,'1 : N',8.16,3.03,.73,.25,{fontSize:12,bold:true,color:C.teal,align:'center'});
  box(s,9.18,2.21,3.1,2.43,C.paleBlue,C.line);
  txt(s,'ClinicalEvent',9.41,2.47,2.64,.38,{fontSize:19,bold:true,color:C.deep,align:'center'});
  ['Diagnosis','Medication','Lab','Note'].forEach((v,i)=>{
    txt(s,v,9.54,2.99+i*.36,2.35,.29,{fontSize:15,color:C.ink,align:'center'});
  });
  box(s,.84,5.35,11.46,.75,C.mint,C.mint);
  txt(s,'对象关系 + 来源标识 = 可判断归属，也能回溯源记录',1.1,5.53,10.94,.37,
    {fontSize:19,bold:true,color:C.deep,align:'center'});
}

// 05 Mapping
{
  const s=base(5,'04 / 映射','用 MEDS 组织患者纵向事件',
    '每条事件包含 subject_id、time、code；数值、文本和血缘字段按需扩展。');
  box(s,.78,2.0,5.07,2.35,C.white,C.line);
  txt(s,'源检验记录',1.04,2.25,4.5,.36,{fontSize:19,bold:true,color:C.deep});
  miniTable(s,[['源字段','值'],['patient_id','P001'],['lab_time','2026-03-01 11:00'],
    ['loinc_code','LOINC:2160-0'],['value / unit','3.9 / mmol/L']],1.03,2.78,4.55,
    [1.67,2.88],.31,C.paleBlue,12);
  arrow(s,5.98,3.18,7.03,3.18,C.teal,2.3);
  box(s,7.18,2,5.1,2.35,C.white,C.teal);
  txt(s,'MEDS 事件',7.44,2.25,4.5,.36,{fontSize:19,bold:true,color:C.teal});
  miniTable(s,[['MEDS 字段','值'],['subject_id','1001'],['time','2026-03-01 11:00'],
    ['code','LAB//LOINC:2160-0//mmol/L'],['numeric_value','3.9']],7.43,2.78,4.58,
    [1.7,2.88],.31,C.mint,12);
  box(s,.78,5.03,11.5,.81,C.deep,C.deep);
  txt(s,'血缘：source_table · source_record_id · site_id · mapping_version',1.05,5.22,10.97,.4,
    {fontSize:17,bold:true,color:C.white,align:'center'});
  caption(s,'静态性别事件允许 time 为空；动态诊疗事件必须保留明确时间。',.84,6.19,11.7);
}

// 06 Workflow
{
  const s=base(6,'05 / 工作流','六个 Agent 协作完成治理',
    '实际执行引擎：LangGraph StateGraph；核心 Schema：MEDS DataSchema.validate。');
  const names=['数据读取','对象提取','跨表关联','MEDS 映射','质量校验','结果汇总'];
  const verbs=['读取','建模','核验','转换','检查','输出'];
  names.forEach((n,i)=>{
    const x=.74+i*2.07;
    box(s,x,2.16,1.77,1.27,i===4?C.mint:C.white,i===4?C.teal:C.line);
    shape(s,ST.ellipse,x+.15,2.3,.33,.33,i===4?C.teal:C.deep);
    txt(s,String(i+1),x+.15,2.3,.33,.33,{fontSize:11,bold:true,color:C.white,align:'center'});
    txt(s,n,x+.2,2.79,1.38,.33,{fontSize:16,bold:true,align:'center'});
    if(i<5) arrow(s,x+1.81,2.79,x+2.02,2.79,C.teal,2.1);
    txt(s,verbs[i],x,3.55,1.77,.29,{fontSize:13,color:C.muted,align:'center'});
  });
  box(s,.78,4.12,5.25,.71,C.deep,C.deep);
  txt(s,'共享 State：数据 + 问题 + 审计',1,4.3,4.8,.34,
    {fontSize:17,bold:true,color:C.white,align:'center'});
  box(s,6.22,4.12,6.12,.71,C.mint,C.mint);
  txt(s,'验证器：MEDS DataSchema.validate',6.45,4.29,5.67,.36,
    {fontSize:17,bold:true,color:C.teal,align:'center'});
  s.addImage({path:images.run,x:.78,y:5.22,w:7.95,h:1.29});
  caption(s,'实际运行截图：Engine / validator / 输出计数',9.05,5.48,3.13);
  txt(s,'6 表 → 17 事件',9.05,5.86,3.14,.45,{fontSize:20,bold:true,color:C.deep});
}

// 07 Audit
{
  const s=base(7,'06 / 证据','每个 Agent 都留下可核对的执行轨迹',
    '数据规模沿审计链路逐步收敛，并保留候选与质量问题。');
  const stages=[['读取','6 张源表'],['对象','4 患者 / 4 就诊'],['关联','13 → 11 候选'],
    ['映射','18 条候选事件'],['校验','17 条合格事件'],['汇总','事件 + 质量报告']];
  stages.forEach(([n,v],i)=>{
    const x=.78+(i%3)*4.19,y=2.05+Math.floor(i/3)*1.17;
    box(s,x,y,3.81,.92,C.white,C.line);
    shape(s,ST.rect,x,y,.1,.92,i===4?C.coral:C.teal);
    txt(s,n,x+.21,y+.12,.78,.31,{fontSize:17,bold:true,color:i===4?C.coral:C.deep});
    txt(s,v,x+1.02,y+.13,2.5,.31,{fontSize:15,color:C.ink});
  });
  box(s,.78,4.55,11.77,.43,C.deep,C.deep);
  txt(s,'audit_log.csv：六个节点依次记录处理摘要',1.0,4.6,11.3,.31,
    {fontSize:16,bold:true,color:C.white});
  s.addImage({path:images.audit,x:.78,y:5.18,w:11.77,h:1.33});
  caption(s,'原始审计截图（局部运行证据）；上方数字与文本均为可编辑元素。',.84,6.64,11.8);
}

// 08 Events
{
  const s=base(8,'07 / 结果','输出是按患者与时间组织的事件流',
    '最终数据覆盖 4 名患者、17 条事件、11 个事件编码。');
  [['4','患者'],['17','事件'],['11','编码']].forEach(([n,l],i)=>{
    const x=.78+i*2.58;
    box(s,x,2.0,2.3,1.02,i===1?C.mint:C.white,i===1?C.teal:C.line);
    txt(s,n,x+.2,2.16,.83,.49,{fontSize:28,bold:true,color:C.teal});
    txt(s,l,x+1.02,2.26,1.03,.31,{fontSize:16,color:C.ink});
  });
  txt(s,'患者 1001 的纵向事件示例',8.58,2.16,3.74,.42,
    {fontSize:17,bold:true,color:C.deep});
  const ev=[['静态','GENDER//F'],['出生','MEDS_BIRTH'],['诊断','ICD10//I10'],
    ['用药','RXNORM:860975'],['检验','LOINC:2160-0  3.9'],['文本','ADMISSION_NOTE']];
  ev.forEach(([t,v],i)=>{
    const x=.79+i*2.08;
    shape(s,ST.ellipse,x,3.47,.2,.2,i===0?C.gold:C.teal);
    if(i<5) line(s,x+.2,3.57,x+2.08,3.57,C.line,2);
    txt(s,t,x,3.85,1.69,.28,{fontSize:13,bold:true,color:i===0?C.gold:C.teal});
    txt(s,v,x,4.18,1.91,.39,{fontSize:11,color:C.ink,valign:'top'});
  });
  box(s,.78,4.88,11.76,.35,C.deep,C.deep);
  txt(s,'静态事件可无时间  ·  动态事件保留原始时间  ·  同一患者连续且有序',.96,4.91,11.35,.27,
    {fontSize:13,bold:true,color:C.white,align:'center'});
  s.addImage({path:images.events,x:.78,y:5.45,w:8.54,h:1.36});
  caption(s,'实际 events.parquet 抽样截图',9.56,5.59,2.63);
  txt(s,'原生事件时间线可直接修改',9.56,5.94,2.76,.57,
    {fontSize:16,bold:true,color:C.deep});
}

// 09 Quality
{
  const s=base(9,'08 / 质量','不臆测补全，把问题留在可追溯链路中',
    '6 项质量问题：3 项错误、3 项告警；按规则阻断、保留或人工复核。');
  const rows=[
    ['规则 / 记录','级别','发现','处置'],
    ['对象关联 D004','错误','就诊 E999 不存在','阻断事件'],
    ['缺失时间 N002','告警','文本无事件时间','不伪造，阻断'],
    ['时间冲突 E004','错误','出院早于入院','记录并复核'],
    ['编码映射 M003','错误','本地码无批准映射','阻断事件'],
    ['数值范围 L003','告警','检验值 9999','保留，人工复核'],
    ['重复事件 L003','告警','同源记录重复','仅保留首条']
  ];
  miniTable(s,rows,.77,1.98,11.8,[2.35,1.1,4.15,4.2],.53,C.deep,14);
  s.addImage({path:images.issues,x:.78,y:6.02,w:5.15,h:1.08});
  txt(s,'质量问题 CSV 原始截图',6.12,6.2,3.2,.37,
    {fontSize:14,color:C.muted});
  pill(s,'错误 3',9.57,6.14,1.13,C.paleCoral,C.coral);
  pill(s,'告警 3',10.87,6.14,1.13,C.paleGold,C.gold);
}

// 10 Deliverables
{
  const s=base(10,'09 / 产物','从源表到可复用的数据资产',
    '输出的数据、元数据与质量证据可共同支持后续受控研究。');
  const out=[
    ['events.parquet','患者维度 MEDS 事件','主数据',C.teal],
    ['codes.parquet','事件编码元数据','元数据',C.deep],
    ['dataset.json','数据集与血缘描述','元数据',C.deep],
    ['quality_issues.csv','质量问题明细','质量证据',C.coral],
    ['audit_log.csv','Agent 处理摘要','审计证据',C.gold]
  ];
  out.forEach(([f,d,t,c],i)=>{
    const y=1.97+i*.84;
    box(s,.8,y,11.7,.64,C.white,C.line);
    shape(s,ST.rect,.8,y,.11,.64,c);
    txt(s,f,1.09,y+.13,3.35,.32,{fontSize:17,bold:true,color:C.deep});
    txt(s,d,4.5,y+.14,5.18,.31,{fontSize:15,color:C.ink});
    txt(s,t,10.38,y+.16,1.76,.27,{fontSize:13,color:c,bold:true,align:'right'});
  });
  box(s,.8,6.38,11.7,.4,C.mint,C.mint);
  txt(s,'医院本地标准化 → 为临床 AI、受控研究和多中心协作提供可信输入',1.03,6.44,11.2,.29,
    {fontSize:14,bold:true,color:C.deep,align:'center'});
}

// 11 Conclusion
{
  const s=base(11,'10 / 结论','先对象化，再事件化，最后校验与发布',
    '实验完成从六张模拟 EMR 源表到可追溯 MEDS 事件数据的治理闭环。');
  const points=[
    ['对象化','明确数据属于谁，以及关联到哪次就诊。',C.deep],
    ['多智能体','六个 LangGraph 节点分工，状态中保留问题与审计。',C.teal],
    ['标准化','17 条 MEDS 事件通过 Schema 校验，覆盖 4 名患者。',C.gold]
  ];
  points.forEach(([h,d,c],i)=>{
    const y=2.05+i*1.26;
    box(s,.82,y,11.65,1.03,C.white,C.line);
    shape(s,ST.rect,.82,y,.12,1.03,c);
    txt(s,h,1.16,y+.26,2.08,.4,{fontSize:20,bold:true,color:c});
    txt(s,d,3.24,y+.24,8.68,.46,{fontSize:17,color:C.ink});
  });
  box(s,.82,6.07,11.65,.57,C.deep,C.deep);
  txt(s,'谢谢聆听  ·  答疑',1.05,6.17,11.18,.37,
    {fontSize:19,bold:true,color:C.white,align:'center'});
}

pptx.writeFile({fileName:path.join(__dirname,'实验03_LangGraph_MEDs数据治理_可编辑.pptx')});
