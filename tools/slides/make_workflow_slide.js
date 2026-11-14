const pptxgen = require('pptxgenjs');
const path = require('path');

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'OpenAI Codex';
pptx.title = '基于 MEDS 标准与 LangGraph 多智能体工作流的医疗事件数据治理';
pptx.subject = '可编辑的医疗事件数据治理技术路线图';
pptx.lang = 'zh-CN';
pptx.theme = {
  headFontFace: 'Microsoft YaHei',
  bodyFontFace: 'Microsoft YaHei',
  lang: 'zh-CN'
};

const S = pptx.ShapeType;
const C = {
  ink: '183645', muted: '607684', line: 'C9D9DF', white: 'FFFFFF',
  bg: 'F8FAFB', blue: '397A9F', blueLite: 'EDF5F9',
  green: '237D70', greenLite: 'EEF7F3', purple: '746199', purpleLite: 'F4F0F9',
  steel: '406D8B', steelLite: 'EEF4F8', amber: 'B87526', amberLite: 'FFF3E5',
  soft: 'DCEBF3'
};
const slide = pptx.addSlide();
slide.background = { color: C.bg };

function rect(x,y,w,h,fill,line=fill,r=0) {
  slide.addShape(r ? S.roundRect : S.rect, {
    x,y,w,h,rectRadius:r,fill:{color:fill},line:{color:line,width:1}
  });
}
function text(t,x,y,w,h,opts={}) {
  slide.addText(t,{x,y,w,h,fontFace:'Microsoft YaHei',fontSize:12,
    color:C.ink,margin:0,breakLine:false,valign:'mid',fit:'shrink',
    ...opts});
}
function line(x1,y1,x2,y2,color=C.line,width=1.2,end='none') {
  const dx=x2-x1, dy=y2-y1;
  slide.addShape(S.line,{x:Math.min(x1,x2),y:Math.min(y1,y2),
    w:Math.abs(dx),h:Math.abs(dy),flipH:dx*dy<0,
    line:{color,width,beginArrowType:'none',endArrowType:end}});
}
function arrow(x1,y1,x2,y2,color=C.ink,width=3.4) {
  line(x1,y1,x2,y2,color,width,'triangle');
}
function circle(x,y,d,fill,lineColor=fill) {
  slide.addShape(S.ellipse,{x,y,w:d,h:d,fill:{color:fill},line:{color:lineColor,width:1}});
}
function card(x,w,title,accent,tint) {
  rect(x,1.35,w,5.16,C.white,C.line,.1);
  rect(x,1.35,w,.55,tint,tint,.1);
  rect(x,1.79,w,.11,tint,tint);
  rect(x,1.35,.065,.55,accent,accent);
  text(title,x+.17,1.47,w-.31,.27,{fontSize:15.4,bold:true,color:accent});
}
function sectionLabel(t,x,y,w,accent) {
  rect(x,y,.045,.23,accent,accent);
  text(t,x+.12,y-.015,w-.12,.26,{fontSize:11.2,bold:true,color:accent});
}
function smallTag(t,x,y,w,fill,accent) {
  rect(x,y,w,.3,fill,fill,.06);
  text(t,x+.07,y+.04,w-.14,.21,{fontSize:9.6,bold:true,color:accent,align:'center'});
}
function sourceRow(y,title,detail) {
  rect(.72,y,2.22,.47,C.white,'D7E5EB',.055);
  circle(.84,y+.14,.17,C.blueLite,C.blue);
  text(title,1.1,y+.055,1.7,.2,{fontSize:10.8,bold:true,color:C.ink});
  text(detail,1.1,y+.254,1.68,.15,{fontSize:8.4,color:C.muted});
}

// Title and route marker are separate editable elements.
rect(.47,.37,.065,.64,C.green);
text('基于 MEDS 标准与 LangGraph 多智能体工作流的医疗事件数据治理',
  .67,.36,12.02,.5,{fontSize:25.4,bold:true,color:C.ink});
text('从异构医疗记录到可追溯的患者纵向事件数据',
  .68,.94,11.8,.2,{fontSize:11.2,color:C.muted});

const modules = [
  {x:.55,w:2.56,title:'数据来源与特征',color:C.blue,tint:C.blueLite},
  {x:3.48,w:2.65,title:'业务建模与 MEDS 规范',color:C.green,tint:C.greenLite},
  {x:6.5,w:3.28,title:'多智能体工作流',color:C.purple,tint:C.purpleLite},
  {x:10.15,w:2.63,title:'结果输出与质量核验',color:C.steel,tint:C.steelLite}
];
modules.forEach(m=>card(m.x,m.w,m.title,m.color,m.tint));

// Main flow connectors stay entirely in the gutters between cards.
arrow(3.15,3.91,3.43,3.91,C.blue,3.4);
arrow(6.17,3.91,6.45,3.91,C.green,3.4);
arrow(9.82,3.91,10.1,3.91,C.purple,3.4);

// Stage 1: six synthetic input categories, each row remains editable.
text('模拟医疗记录同时包含结构化字段与临床文本；标识、时间和术语存在差异。',
  .72,2.04,2.2,.49,{fontSize:9.7,color:C.muted,valign:'top'});
[
  ['患者信息','基本信息、人口学特征'],
  ['就诊记录','时间、科室、就诊类型'],
  ['诊断编码','诊断编码与描述'],
  ['用药记录','药品、剂量与用法'],
  ['检验检查','指标、数值与报告'],
  ['出院记录','出院小结、病历文本']
].forEach(([a,b],i)=>sourceRow(2.61+i*.49,a,b));
smallTag('结构化数据',.72,5.75,1.02,C.blueLite,C.blue);
smallTag('非结构化文本',1.85,5.75,1.09,C.blueLite,C.blue);
text('来源：脚本内模拟数据',.73,6.18,2.18,.17,{fontSize:8.2,color:C.muted});

// Stage 2: object graph and MEDS field mapping.
sectionLabel('构建逻辑数据对象',3.67,2.05,2.26,C.green);
rect(4.29,2.46,1.03,.42,C.greenLite,C.green,.07);
text('患者',4.49,2.54,.63,.22,{fontSize:11.2,bold:true,color:C.green,align:'center'});
const objectNodes = [
  {t:'就诊',x:3.71,y:3.03}, {t:'诊断',x:4.96,y:3.03},
  {t:'用药',x:3.71,y:3.63}, {t:'检验检查',x:4.96,y:3.63}
];
objectNodes.forEach(n=>{
  rect(n.x,n.y,1.05,.39,C.white,'B8D8CF',.05);
  text(n.t,n.x+.06,n.y+.08,.93,.2,{fontSize:10.2,align:'center'});
});
line(4.46,2.89,4.22,3.03,C.green,1.3);
line(5.11,2.89,5.48,3.03,C.green,1.3);
line(4.47,2.89,4.23,3.63,C.green,1.3);
line(5.11,2.89,5.48,3.63,C.green,1.3);
arrow(4.8,4.11,4.8,4.41,C.green,1.9);
sectionLabel('映射为 MEDS 事件',3.67,4.52,2.28,C.green);
rect(3.68,4.92,2.24,1.19,C.greenLite,'B8D8CF',.055);
text('核心字段',3.82,5.04,1.87,.2,{fontSize:10,bold:true,color:C.green});
text('subject_id  ·  time  ·  code',3.82,5.3,1.96,.24,
  {fontSize:10.2,bold:true,color:C.ink});
text('可选：numeric_value、text_value',3.82,5.62,2.02,.19,
  {fontSize:8.7,color:C.muted});
text('扩展：就诊标识与来源字段',3.82,5.86,2.02,.17,
  {fontSize:8.7,color:C.muted});

// Stage 3: a connected LangGraph chain with a shared state indicator.
smallTag('LangGraph · 共享 State',6.69,2.01,2.88,C.purpleLite,C.purple);
text('传递事件、来源与待核验问题',6.75,2.37,2.78,.2,
  {fontSize:9.4,color:C.muted,align:'center'});
const steps = [
  ['数据读取','多源表格与临床文本'],
  ['实体与事件提取','患者、就诊、诊疗事件'],
  ['跨表关联','标识、时间与业务规则'],
  ['MEDS 标准映射','转换为标准事件记录'],
  ['数据校验','字段、编码、关联与时间']
];
steps.forEach(([name,detail],i)=>{
  const y=2.73+i*.66;
  rect(6.77,y,2.73,.53,C.white,'CFC4DE',.06);
  circle(6.88,y+.13,.25,C.purple);
  text(String(i+1),6.88,y+.135,.25,.22,
    {fontSize:9.4,bold:true,color:C.white,align:'center'});
  text(name,7.23,y+.065,2.09,.2,{fontSize:11.2,bold:true,color:C.ink});
  text(detail,7.23,y+.291,2.09,.16,{fontSize:8.8,color:C.muted});
  if(i<4) arrow(8.13,y+.54,8.13,y+.66,C.purple,1.7);
});
line(6.67,2.75,6.67,5.96,'B6A5CD',1.1);
line(6.67,2.98,6.75,2.98,'B6A5CD',1.1);
line(6.67,5.64,6.75,5.64,'B6A5CD',1.1);
text('状态贯穿各节点',6.57,6.13,3.08,.19,
  {fontSize:8.9,color:C.purple,align:'center'});

// Stage 4: native PowerPoint table and separate quality status rows.
sectionLabel('标准化数据',10.33,2.04,2.22,C.steel);
text('按患者组织、按事件时间排序的 MEDS Parquet。',
  10.34,2.33,2.2,.36,{fontSize:9.2,color:C.muted,valign:'top'});
slide.addTable([
  ['subject_id','time','code'],
  ['10001','01-01','D001'],
  ['10001','01-02','M001'],
  ['10002','01-03','L001']
],{
  x:10.34,y:2.76,w:2.25,h:1.16,
  colW:[.85,.7,.7],fontSize:8.4,
  color:C.ink,fontFace:'Aptos'
});
text('结构示例；编码并非 MEDS 规定值',10.34,3.99,2.25,.18,
  {fontSize:8.2,color:C.muted});
sectionLabel('质量核验结果',10.33,4.34,2.22,C.steel);
const checks = [
  ['必需字段检查',false],['编码映射检查',false],
  ['对象关联检查',false],['时间一致性检查',false],
  ['缺失与冲突标记',true]
];
checks.forEach(([t,warn],i)=>{
  const y=4.73+i*.27;
  circle(10.36,y+.03,.15,warn?C.amberLite:C.greenLite,warn?C.amber:C.green);
  text(warn?'!':'✓',10.37,y+.02,.13,.16,
    {fontSize:8.4,bold:true,color:warn?C.amber:C.green,align:'center'});
  text(t,10.61,y,1.94,.2,{fontSize:9.9,color:C.ink});
});
rect(10.34,6.13,2.25,.24,C.amberLite,C.amberLite,.04);
text('缺失与冲突只标记，不臆测补全',10.42,6.16,2.08,.16,
  {fontSize:8.2,bold:true,color:C.amber,align:'center'});

// Summary bar and footer are independent native elements.
rect(.55,6.69,12.23,.48,C.soft,C.soft,.08);
slide.addShape(S.can,{x:.76,y:6.78,w:.25,h:.28,
  fill:{color:C.steelLite},line:{color:C.steel,width:1.1}});
text('统一、可追溯的患者纵向医疗事件数据  →  为后续医疗 AI 研究任务提供数据基础',
  1.21,6.79,11.05,.25,
  {fontSize:14.8,bold:true,color:C.ink,align:'center'});
text('技术路线示意  |  输入与表格数据均为模拟示例',
  .59,7.27,12.16,.14,{fontSize:8.4,color:C.muted,align:'right'});

pptx.writeFile({fileName:path.join(__dirname,'MEDS_LangGraph_Editable_Workflow.pptx')});
