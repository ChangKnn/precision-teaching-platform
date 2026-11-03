(() => {
  const levels = [
    ['U 单点结构', '聚焦并处理一个相关信息、对象或操作。', '识别、指认、命名、画出、找到、标注、执行一个简单程序'],
    ['M 多点结构', '处理多个相关信息，但尚未形成稳定联系。', '列举、概述、组合、区别、执行多个操作'],
    ['R 关联结构', '组织多个相关信息，建立并处理它们之间的关系。', '排序、分类、比较与对照、解释原因或结果、部分—整体分析、建立类比、组织、区分、建立关系'],
    ['EA 拓展抽象结构', '超越当前材料或情境，对已有关系进行抽象、迁移、检验或重构。', '定义、预测、评价、提出假设、理论化、规划、论证、创作、建立模型、开发、创造']
  ];
  const paths = [
    {name:'递进推进型', crop:'20 70 398 104', fit:'学习有明确的前后依赖，后一活动需要前一活动形成的认识。', plan:'先建立基础，再按需安排支持、协作、独立思考与迁移，最后共同提升。中间环节可合并或跨课时展开。'},
    {name:'主线嵌入型', crop:'20 250 398 160', fit:'多数学生能继续主任务，少数学生有可短时补足的局部困难。', plan:'多数学生持续学习，教师对少数学生提供小组或个别支持，随后回归主任务；最后全班交流。'},
    {name:'并行分层型', crop:'445 25 411 170', fit:'存在几个清晰的临时学习需要群体，仍共享同一核心目标。', plan:'全班定向后，各组同时完成不同复杂度或支架的任务，再共同汇聚。分组按当前需要调整，不固定为低、中、高。'},
    {name:'站点轮转型', crop:'445 237 411 173', fit:'学习需要有多种类型，内容能拆为功能不同、相对独立的模块。', plan:'设置指导、协作、对话等学习站，按需要选择和轮转后全班整合。站内可小组或个体学习，不要求人人完成所有站。'},
    {name:'个体自驱型', crop:'445 456 411 164', fit:'学生的困难高度个体化，适合通过解释、对话和修订持续推进。', plan:'全班定向后，学生分别在AI等支持下表达、获得反馈、修订；教师巡视并介入困难，最后共同提升。'}
  ];
  const contents = {
    'goal-rules': {
      title:'目标设计的基本规则', compact:true,
      html:'<p class="goal-guide-lead">围绕共同核心目标，根据诊断判断学生“当前在哪里、下一步往哪里发展”，确定适合的阶段性进阶目标。</p><section><h3>共同核心目标：明确全班的发展方向</h3><p>结合课程标准或课程大纲、教材与本课要求，并依据班级诊断校准目标的起点和挑战程度。学生已经达到的表现，可以进一步深化。</p></section><section><h3>阶段性进阶目标：确定近期发展任务</h3><p>依据学生当前思维结构与目标之间的差距，明确下一步需要形成哪些认识和联系。差异化任务与支持服务于共同目标，不把学生分到彼此割裂的终点，也不把近期目标当作学习上限。</p></section>'
    },
    'path-rules': {
      title:'为什么先设计课堂活动路径？', compact:true,
      html:'<p class="goal-guide-lead">精准教学目标明确了学生“需要往哪里发展”。课堂活动路径则进一步回答：“一堂课怎样组织，才能帮助不同学生逐步接近并实现目标？”</p><p>在细化具体学习活动之前，教师先依据共同核心目标、学生的阶段性进阶目标和班级学习差异，整体规划全班、小组与个体活动的推进方式。</p><p>这一步要明确：活动按什么顺序开展，哪些活动同时进行，学生何时分组或独立学习、何时回到全班交流整合。由此形成一条有明确认知发展方向的课堂活动序列，再逐一细化任务与支持。</p>'
    },
    levels: {
      title:'目标表述参考', compact:false,
      html:'<div class="goal-guide-table-wrap"><table class="goal-guide-levels"><thead><tr><th scope="col">目标层级</th><th scope="col">核心认知特征</th><th scope="col">典型学习动词</th></tr></thead><tbody>'+levels.map(row=>'<tr><th scope="row">'+row[0]+'</th><td>'+row[1]+'</td><td>'+row[2]+'</td></tr>').join('')+'</tbody></table></div><p class="goal-guide-note">动词仅供表述参考。</p>'
    },
    paths: {
      title:'典型课堂路径策略卡', compact:false,
      html:'<p class="goal-guide-lead">结合差异化教学案例与课堂组织需要，按活动如何组合、学生如何在活动之间流动，可概括出五种典型路径：递进推进型、主线嵌入型、并行分层型、站点轮转型和个体自驱型。</p><p class="goal-guide-lead">这些路径是可供参考的课堂组织方式，并非相互排斥的固定模式。教师可依据班级诊断呈现的差异、目标的进阶关系，以及内容与任务特征选择主要路径，再结合课时与课堂条件灵活组合、调整。</p><div class="goal-guide-path-grid">'+paths.map((path,index)=>'<article class="goal-guide-path-card"><h3>'+path.name+'</h3><svg class="goal-guide-path-figure" viewBox="'+path.crop+'" role="img" aria-label="'+path.name+'典型组织示意图"><defs><clipPath id="goal-guide-crop-'+index+'"><rect x="'+path.crop.split(' ')[0]+'" y="'+path.crop.split(' ')[1]+'" width="'+path.crop.split(' ')[2]+'" height="'+path.crop.split(' ')[3]+'" /></clipPath></defs><image href="/assets/teaching-pathways-original.png" width="865" height="625" clip-path="url(#goal-guide-crop-'+index+')" /></svg><p><b>适用：</b>'+path.fit+'</p><p><b>安排：</b>'+path.plan+'</p></article>').join('')+'</div><p class="goal-guide-note">C：全班共同；H：同质小组；X：异质小组；I：个体；S：学习站；A：综合应用，可由小组或个体完成。单课时小组活动一般一次、最多两次；同时开展多个小组只算一次。</p>'
    }
  };

  function openGuide(name) {
    const content = contents[name];
    if (!content) return;
    const dialog = document.getElementById('goalPathGuideDialog');
    document.getElementById('goalPathGuideTitle').textContent = content.title;
    document.getElementById('goalPathGuideBody').innerHTML = content.html;
    dialog.classList.toggle('goal-guide-compact', content.compact);
    if (!dialog.open) dialog.showModal();
    dialog.scrollTop = 0;
  }

  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-goal-guide]');
    if (opener) openGuide(opener.dataset.goalGuide);
    if (event.target.closest('[data-close-goal-guide]')) document.getElementById('goalPathGuideDialog').close();
  });
  const dialog = document.getElementById('goalPathGuideDialog');
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
})();
