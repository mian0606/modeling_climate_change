/* 所有图形使用同目录 D3；历史数据来自 export_yangtze.py。 */
const $ = s => d3.select(s).node();
const water = d3.select('#water');
function mechanism() {
  const stress=+$('#stress').value/100, level=125-90*stress;
  water.selectAll('*').remove();
  water.append('rect').attr('x',68).attr('y',130).attr('width',540).attr('height',145).attr('rx',8).attr('fill','#e8dfca');
  water.append('rect').attr('x',68).attr('y',275-level).attr('width',540).attr('height',level).attr('fill','#7fa9ae').attr('opacity',.65);
  water.append('path').attr('d','M68,130V275H608V130').attr('fill','none').attr('stroke','#8b9a8a').attr('stroke-width',2);
  for(let i=0;i<8;i++) water.append('path').attr('d',`M${95+i*30},70l-5,15`).attr('stroke','#568c9c').attr('stroke-width',3).attr('opacity',1-stress*.85);
  water.append('circle').attr('cx',550).attr('cy',55).attr('r',24+stress*10).attr('fill',d3.interpolateRgb('#d7b56b','#c45a35')(stress));
  for(let i=0;i<4;i++) water.append('path').attr('d',`M${370+i*35},115q-12,-20 0,-40q12,-20 0,-35`).attr('fill','none').attr('stroke','#b96b45').attr('stroke-width',1+stress*3).attr('opacity',.3+stress*.6);
  water.append('path').attr('d','M260,157V67M260,115Q208,105 224,76Q259,75 260,115M260,95Q303,83 295,57Q260,58 260,95').attr('fill',d3.interpolateRgb('#477959','#9a9b62')(stress)).attr('stroke','#477959').attr('stroke-width',4);
  water.append('text').attr('x',100).attr('y',45).text('降水补给').attr('fill','#457487');
  water.append('text').attr('x',363).attr('y',22).text('蒸发需求').attr('fill','#9b5437');
  water.append('text').attr('x',93).attr('y',255).text('土壤水库').attr('fill','#354b47');
  water.append('text').attr('x',280).attr('y',156).attr('font-size',12).text('根系取水 → 光合作用');
  $('#mechanism-copy').textContent=stress<.45?'水分充足时，植物能通过蒸腾散热，并进行光合作用。': '高温增加大气对水分的需求；若降水补给不足，土壤会失水。缺水使气孔关闭、光合作用受限，也削弱蒸发冷却。实际蒸散并不会随温度无限增加。';
}
$('#stress').addEventListener('input',mechanism); mechanism();
(async function load(){
  let data;
  try { data=await d3.json('yangtze.json');
    if(!data.frames?.length || !data.lat?.length || !data.lon?.length) throw Error('数据结构不完整');
  } catch(error) {
    $('#status').className='error';
    $('#status').textContent='无法读取 yangtze.json。请先运行 python build_json.py --only yangtze。'+error.message;
    console.info(error); return;
  }
  $('#status').textContent=''; $('#controls').hidden=false;
  const slider=$('#year'), frames=data.frames;
  slider.min=0;slider.max=frames.length-1;slider.step=1;slider.value=frames.length-1;
  if(!data.availability.lai) { $('#leaf-mask').disabled=true; $('#leaf-mask').parentElement.title='此文件没有 LAI'; }
  const svg=d3.select('#map'), bounds={left:48,right:730,top:35,bottom:395};
  const x=d3.scaleLinear().domain([99.3,122.7]).range([bounds.left,bounds.right]);
  // 经纬等距区域图；经度按区域中纬度缩放后的宽高比布局。
  const y=d3.scaleLinear().domain([23.3,35.7]).range([bounds.bottom,bounds.top]);
  svg.append('g').attr('class','axis').attr('transform',`translate(0,${bounds.bottom})`).call(d3.axisBottom(x).ticks(6).tickFormat(v=>`${v}°E`));
  svg.append('g').attr('class','axis').attr('transform',`translate(${bounds.left},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(v=>`${v}°N`));
  const cells=data.lat.flatMap((lat,j)=>data.lon.map((lon,i)=>({lat,lon,index:j*data.lon.length+i})));
  const dx=data.lon.length>1?Math.abs(data.lon[1]-data.lon[0]):1.25;
  const dy=data.lat.length>1?Math.abs(data.lat[1]-data.lat[0]):.94;
  const tile=svg.append('g').selectAll('rect').data(cells).join('rect').attr('class','cell')
    .attr('x',d=>x(d.lon-dx/2)).attr('y',d=>y(d.lat+dy/2))
    .attr('width',x(dx)-x(0)).attr('height',y(0)-y(dy));
  const maxGpp=Math.max(1,data.domains.gpp[1]);
  const radius=d3.scaleSqrt().domain([0,maxGpp]).range([0,Math.min(x(dx)-x(0),y(0)-y(dy))*.43]);
  const dots=svg.append('g').selectAll('circle').data(cells).join('circle').attr('class','dot').attr('cx',d=>x(d.lon)).attr('cy',d=>y(d.lat));
  // 地标定位，不作为流域边界。
  const cities=[['重庆',106.55,29.56],['武汉',114.31,30.59],['南京',118.80,32.06],['上海',121.47,31.23]];
  const city=svg.append('g').selectAll('g').data(cities).join('g').attr('class','city').attr('transform',d=>`translate(${x(d[1])},${y(d[2])})`);
  city.append('circle').attr('r',2.5);city.append('text').attr('x',5).attr('y',-7).text(d=>d[0]);
  let layer='temperature',current, timer=null;
  const fmt=v=>v==null?'缺失':d3.format(',.1f')(v);
  function visible(d){ return !$('#leaf-mask').checked || (data.reference.lai?.[d.index]!=null && data.reference.lai[d.index]>=3); }
  function detail(d){
    if(!visible(d)) { $('#detail').textContent='此格低于固定 LAI 门槛，或基准 LAI 缺失。';return; }
    const i=d.index, b=data.reference;
    const diff=(v,ref)=>v==null||ref==null?'缺失':d3.format('+.1f')(v-ref);
    $('#detail').textContent=`${current.year} 夏季 · ${d.lat.toFixed(2)}°N, ${d.lon.toFixed(2)}°E ｜ 气温 ${fmt(current.temperature[i])} °C（较基准 ${diff(current.temperature[i],b.temperature[i])} °C）｜ 表层土壤水 ${fmt(current.soil[i])} kg/m²（${diff(current.soil[i],b.soil[i])}）｜ GPP ${fmt(current.gpp[i])} g C/m²/夏季（${diff(current.gpp[i],b.gpp[i])}）`;
  }
  tile.attr('tabindex',0).attr('role','button').attr('aria-label',d=>`${d.lat.toFixed(2)}北纬 ${d.lon.toFixed(2)}东经 格点`)
    .on('pointerenter',(_,d)=>detail(d)).on('click',(_,d)=>detail(d)).on('focus',(_,d)=>detail(d));
  function draw(){
    current=frames[+slider.value];$('#year-label').value=current.year;
    const domain=data.domains[layer], color=d3.scaleSequential(layer==='temperature'?d3.interpolateYlOrRd:d3.interpolateBlues).domain(domain);
    tile.attr('fill',d=>visible(d)&&current[layer][d.index]!=null?color(current[layer][d.index]):'transparent');
    dots.attr('r',d=>visible(d)&&current.gpp[d.index]!=null?radius(Math.max(0,current.gpp[d.index])):0);
    const legend=d3.select('#legend');legend.selectAll('*').remove();
    const bar=legend.append('svg').attr('width',280).attr('height',51);
    const scale=d3.scaleLinear().domain(domain).range([8,258]);
    bar.selectAll('rect').data(d3.range(100)).join('rect').attr('x',i=>8+i*2.5).attr('y',2).attr('width',2.6).attr('height',10).attr('fill',i=>color(domain[0]+(domain[1]-domain[0])*i/99));
    bar.append('g').attr('transform','translate(0,13)').call(d3.axisBottom(scale).ticks(4));
    bar.append('text').attr('x',8).attr('y',47).text(layer==='temperature'?'夏季平均气温 · °C':'夏季平均表层土壤水 · kg/m²');
    const c=legend.append('svg').attr('width',250).attr('height',51);
    const vals=[maxGpp/4,maxGpp/2,maxGpp];
    c.selectAll('circle').data(vals).join('circle').attr('cx',(_,i)=>25+i*77).attr('cy',16).attr('r',radius).attr('fill','none').attr('stroke','#111');
    c.selectAll('text').data(vals).join('text').attr('x',(_,i)=>25+i*77).attr('y',47).attr('text-anchor','middle').text(v=>Math.round(v));
    legend.append('span').text('黑圈面积 ∝ GPP · g C/m²/夏季；空白表示缺失或被筛选。');
  }
  slider.addEventListener('input',draw);
  document.querySelectorAll('[name=layer]').forEach(e=>e.addEventListener('change',()=>{layer=e.value;draw();}));
  $('#leaf-mask').addEventListener('change',draw);
  function stop(){clearInterval(timer);timer=null;$('#play').textContent='播放';}
  $('#play').addEventListener('click',()=>{if(timer){stop();return;}$('#play').textContent='暂停';timer=setInterval(()=>{slider.value=(+slider.value+1)%frames.length;draw();},650);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  draw();
})();
