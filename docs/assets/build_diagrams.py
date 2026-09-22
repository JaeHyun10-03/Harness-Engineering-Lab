"""Generate actual top-down flowcharts (DOT → SVG/HTML).
Rendering: install @viz-js/viz in a tooling directory, then run
python3 build_diagrams.py --viz-module /absolute/path/node_modules/@viz-js/viz/dist/viz.js
No application or harness runtime dependency is added.
"""
from pathlib import Path
import argparse, json, subprocess
from html import escape
ROOT=Path(__file__).resolve().parent
GRAPHS=[]
class Flow:
    def __init__(self,name,title,note):
        self.name,self.title,self.note=name,title,note;self.nodes={};self.edges=[];GRAPHS.append(self)
    def n(self,key,label,kind='box'):
        assert key not in self.nodes
        self.nodes[key]=(label,kind);return self
    def e(self,a,b,label=''):
        self.edges.append((a,b,label));return self
    def dot(self):
        out=['digraph G {','graph [rankdir=TB, bgcolor="white", pad="0.35", nodesep="0.8", ranksep="0.9", splines=ortho, outputorder=edgesfirst];',
             'node [shape=box, style="rounded,filled", fillcolor="#f1f6f3", color="#a9beb5", fontname="Arial", fontsize=18, margin="0.22,0.15", penwidth=1.3];',
             'edge [color="#71867e", fontcolor="#4f625a", fontname="Arial", fontsize=15, penwidth=1.4, arrowsize=0.8];']
        for key,(label,kind) in self.nodes.items():
            options={'box':'', 'decision':', shape=diamond, style=filled, fillcolor="#f9f5e9", color="#c4b894", margin="0.14,0.09"',
                     'end':', shape=oval, fillcolor="#eaf1f7", color="#a9bdcc"','pause':', shape=oval, fillcolor="#f3ede6", color="#c4b39b"','sub':', shape=box, peripheries=2'}[kind]
            group=', group=main' if key in MAIN_PATHS[self.name] else ''
            out.append(f'{key} [label={json.dumps(label,ensure_ascii=False)}{options}{group}];')
        # Align short side branches with their decision, keeping the main rail vertical.
        aligned=set()
        for a,b,label in self.edges:
            if self.nodes[a][1]=='decision' and b not in MAIN_PATHS[self.name] and b not in aligned:
                incoming=[x for x,y,_ in self.edges if y==b]
                if len(incoming)==1 and a!=b and (a in MAIN_PATHS[self.name] or self.nodes[b][1] in ('end','pause')) and self.nodes[b][1]!='decision':
                    out.append(f'{{ rank=same; {a}; {b}; }}')
                    aligned.add(b)
        for a,b,label in self.edges:
            assert a in self.nodes and b in self.nodes
            main=MAIN_PATHS[self.name]
            weight=30 if (a,b) in list(zip(main,main[1:])) else 1
            # Diamond edges must use cardinal vertices, never automatic face clipping.
            ports=[]
            if self.nodes[a][1]=='decision':
                outgoing=[edge[1] for edge in self.edges if edge[0]==a]
                preferred=next((target for target in outgoing if (a,target) in list(zip(main,main[1:]))), outgoing[0])
                alternatives=[target for target in outgoing if target!=preferred]
                tail='s' if b==preferred else ['e','w','n'][alternatives.index(b)]
                ports.append(f'tailport={tail}, tailclip=false')
            if self.nodes[b][1]=='decision':
                # Return paths arrive from the side; forward paths enter at the top.
                order=list(self.nodes)
                backwards=(a in main and b in main and main.index(a)>main.index(b)) or (a not in main and order.index(a)>order.index(b))
                ports.append(('headport=w' if backwards else 'headport=n')+', headclip=false')
            port_options=(','+', '.join(ports)) if ports else ''
            out.append(f'{a} -> {b} [xlabel={json.dumps(label,ensure_ascii=False)}, weight={weight}{port_options}];')
        out.append('}')
        # Every decision must expose labeled alternatives; no silent fall-through.
        for key,(_,kind) in self.nodes.items():
            es=[e for e in self.edges if e[0]==key]
            if kind=='decision':assert len(es)>=2 and all(e[2] for e in es),key
            if kind in ['end','pause']:continue
            assert es,(self.name,key)
        return '\n'.join(out)

f=Flow('workflow','시작부터 완료까지','이중 테두리는 아래 단계별 그림으로 자세히 설명합니다. 명령은 메인이 호출하며 verifier는 읽기 전용입니다.')
f.n('start','시작 · 지침과 현재 작업 안내','end').n('kind','요청 종류는?','decision')
f.n('question','관련 자료 확인 → 답변','end').n('reviewonly','읽기 전용 리뷰 → 보고','end').n('improve','결정·설정·실험 확인\n개선 범위 정하기','end')
f.n('plan','계획·재개와 계획 검증\n상세 흐름: 3단계','sub').n('proceed','계획 확정 +\n구현 요청이 있는가?','decision').n('planend','계획 보고 또는 선택 대기\n구현하지 않음','pause')
f.n('budget','결과 리뷰 회차가\n남아 있는가?','decision').n('build','start → 구현·기록\n상세 흐름: 4단계','sub').n('test','일반·필요한 성능 테스트\n상세 흐름: 5단계','sub').n('tested','필수 테스트 통과 +\n증거가 최신인가?','decision')
f.n('recover','지금 수정·복구\n가능한가?','decision').n('blocked','wait / block\n원인·재개 조건 보고','pause')
f.n('review','review-begin → verifier\n상세 흐름: 6단계','sub').n('verdict','리뷰 결과는?','decision').n('pass','review pass 등록').n('fail','review fail 등록').n('callfail','block / wait\n호출 불가·근거 부족·이견 보고','pause')
f.n('complete','complete\n상태·최신 테스트·리뷰 증거 확인').n('valid','완료 조건 충족?','decision').n('done','done → 결과 보고\n응답 종료는 Stop Hook 확인','end')
f.n('wait','한도 도달 → waiting\n남은 문제·선택지 보고').n('extend','사용자가 추가 리뷰\n1회를 허락했는가?','decision').n('hold','미완료로 보류','pause').n('extra','review-extend\n한도만 1회 추가·횟수 유지')
for e in [('start','kind',''),('kind','question','질문'),('kind','reviewonly','리뷰만'),('kind','improve','하네스 개선'),('kind','plan','개발'),('plan','proceed',''),('proceed','planend','아니오'),('proceed','budget','예'),('budget','build','예'),('budget','wait','아니오'),('build','test',''),('test','tested',''),('tested','review','예'),('tested','recover','아니오'),('recover','budget','가능'),('recover','blocked','불가·선택 필요'),('review','verdict',''),('verdict','pass','통과 권고·필수 근거 충족'),('verdict','fail','실제 결함'),('verdict','callfail','호출 불가·근거 부족·이견'),('fail','budget','수정 전 회차 확인'),('pass','complete',''),('complete','valid',''),('valid','done','예'),('valid','recover','아니오'),('wait','extend',''),('extend','extra','명시적 허락'),('extend','hold','없음·보류'),('extra','budget','')]:f.e(*e)

f=Flow('step-1-start','1. 시작','지침 로딩과 Hook 연결의 개념도이며 Claude Code 내부의 정확한 파일 로딩 순서를 뜻하지 않습니다.')
f.n('start','세션 시작·재개','end').n('load','CLAUDE.md·rules 로딩\nsettings.json 처리').n('enabled','SessionStart Hook이\n활성화되어 있는가?','decision').n('skip','시작 Hook 안내 없음\n설정·연결 확인','pause').n('call','hook-session 실행').n('state','현재 작업이 있는가?','decision').n('active','작업 ID·단계\n읽을 문서 위치 안내').n('none','기본 지침·작업 목록 위치 안내').n('next','요청 분류로\n문서 본문은 필요할 때 읽기','end')
for e in [('start','load',''),('load','enabled',''),('enabled','skip','아니오'),('enabled','call','예'),('call','state',''),('state','active','있음'),('state','none','없음'),('active','next',''),('none','next','')]:f.e(*e)

f=Flow('step-2-route','2. 요청 분류','분류는 메인이 CLAUDE.md 지침에 따라 판단합니다. Python이 사용자 요청을 자동 분류하는 기능은 아닙니다.')
f.n('start','사용자 요청·요청별 지침 읽기','end').n('kind','어떤 요청인가?','decision').n('q','관련 자료 확인 → 답변','end').n('r','review-task\n읽기 전용 검증 → 보고','end').n('h','결정·실험·관련 설정 확인\n개선 범위 정하기','end').n('new','새 작업인가?','decision').n('p','plan-task → 새 계획','end').n('old','status·task.md·progress.md\n실제 코드 대조').n('clear','기존 작업과 요청의\n관계가 명확한가?','decision').n('resume','implement-task → 재개','end').n('ask','사용자에게 관계·범위 확인','pause')
for e in [('start','kind',''),('kind','q','질문'),('kind','r','리뷰만'),('kind','h','하네스 개선'),('kind','new','개발'),('new','p','예'),('new','old','아니오'),('old','clear',''),('clear','resume','예'),('clear','ask','아니오')]:f.e(*e)

f=Flow('step-3-plan','3. 계획·재개','계획 리뷰는 최초 1회 + 재리뷰 2회를 지침으로 관리합니다. 결과 리뷰의 프로그램 카운터와 별개입니다.')
f.n('start','작업 목록·상태·관련 코드 확인','end').n('new','새 작업인가?','decision').n('create','new → 작업 문서 생성').n('resume','기존 기록과 실제 코드 대조').n('write','목표·범위·완료 기준 작성\n영역별 테스트 기준·명령·증거 연결').n('choice','중요한 결정이\n남아 있는가?','decision').n('ask','wait → 장단점·선택지 보고\n사용자 결정 대기','pause').n('perf','성능 테스트가 필요한\n기존 기능 개선인가?','decision').n('base','구현 전 기준 성능 측정\n동일한 비교 조건 기록').n('budget','계획 리뷰가\n3회 미만인가?','decision').n('limit','자동 수정 중단\n남은 문제·선택지 보고','pause').n('v','verifier 계획 검증\n호출 회차·실제 응답 보존').n('result','검증 결과는?','decision').n('fix','필수 누락 보완\n선택적 개선은 완료 차단 제외').n('fail','block / wait\n호출 불가·근거 부족·이견 보고','pause').n('implement','구현까지 요청받았는가?','decision').n('go','계획 확정 → 구현','end').n('end','계획 결과 보고 후 종료','end')
for e in [('start','new',''),('new','create','예'),('new','resume','아니오'),('create','write',''),('resume','write',''),('write','choice',''),('choice','ask','예'),('choice','perf','아니오'),('perf','base','예'),('perf','budget','아니오'),('base','budget',''),('budget','limit','아니오'),('budget','v','예'),('v','result',''),('result','fix','필수 누락'),('fix','write','계획 수정 후 재검증'),('result','fail','호출 불가·판단 불가'),('result','implement','통과 권고'),('implement','go','예'),('implement','end','아니오')]:f.e(*e)

f=Flow('step-4-build','4. 구현','PreToolUse는 Edit·Write의 지정 경로만 제한합니다. Bash·외부 편집 등 모든 수정 경로를 통제하지 않습니다.')
f.n('start','작업 문서·구현 스킬·관련 코드 읽기','end').n('room','리뷰 회차가 남아 있는가?','decision').n('wait','waiting → 사용자 결정 대기','pause').n('ready','목표·범위·완료 기준이\n작성되어 있는가?','decision').n('fill','계획 보완').n('begin','start → implementing').n('edit','파일 수정 요청').n('tool','Edit·Write 호출인가?','decision').n('path','app / scripts / tests\n아래 파일인가?','decision').n('phase','현재 구현 단계인가?','decision').n('deny','호출 거부\n계획·상태 확인').n('allowed','수정 실행\n해당 Hook은 차단하지 않음').n('record','진행 기록 갱신').n('problem','선택·환경 문제가 있는가?','decision').n('stop','wait / block\n미완료·재개 조건 보고','pause').n('more','구현할 내용이 남았는가?','decision').n('done','테스트 단계로','end')
for e in [('start','room',''),('room','wait','아니오'),('room','ready','예'),('ready','fill','아니오'),('fill','ready','보완 후'),('ready','begin','예'),('begin','edit',''),('edit','tool',''),('tool','allowed','아니오'),('tool','path','예'),('path','allowed','아니오'),('path','phase','예'),('phase','allowed','예'),('phase','deny','아니오'),('deny','room','재개 조건 확인'),('allowed','record',''),('record','problem',''),('problem','stop','예'),('problem','more','아니오'),('more','edit','예'),('more','done','아니오')]:f.e(*e)

f=Flow('step-5-test','5. 테스트','일반→성능 순서는 등록한 통합 명령의 책임입니다. verify는 checks 배열을 자동 조기 중단하지 않습니다. 테스트 수정 반복 자체에는 별도 횟수 제한이 없습니다.')
f.n('start','task.md·checks.json 확인','end').n('room','리뷰 회차가 남아 있는가?','decision').n('wait','waiting → 사용자 결정 대기','pause').n('config','현재 단계·필수 명령·환경이\n실행 가능한가?','decision').n('setup','설정·환경 복구 또는 block\n준비 후 이 단계 다시 시작','pause').n('before','verify → 테스트 전 코드 식별\n일반 테스트 실행').n('normal','일반 테스트 통과?','decision').n('need','성능 테스트가 필요한가?','decision').n('perf','통합 명령이 성능 테스트 실행').n('target','성능 목표 충족?','decision').n('record','종료 코드·로그·결과 저장').n('stable','테스트 도중 코드 변경 없이\n필수 명령 모두 성공했는가?','decision').n('fail','실패 기록 → implementing\n환경 문제는 block').n('can','지금 수정·복구 가능한가?','decision').n('pause','미완료·재개 조건 보고','pause').n('fix','구현으로 돌아가 수정').n('done','reviewing → 결과 리뷰','end')
for e in [('start','room',''),('room','wait','아니오'),('room','config','예'),('config','setup','아니오'),('config','before','예'),('before','normal',''),('normal','fail','아니오'),('normal','need','예'),('need','record','아니오: 계획에 이유 기록'),('need','perf','예'),('perf','target',''),('target','fail','아니오·미실행'),('target','record','예'),('record','stable',''),('stable','done','예'),('stable','fail','아니오'),('fail','can',''),('can','pause','아니오'),('can','fix','예'),('fix','room','재테스트 전 확인')]:f.e(*e)

f=Flow('step-6-review','6. 리뷰','결과 리뷰 기본 한도는 3회입니다. 시작 시 회차를 소비하며 호출 실패도 횟수에 포함됩니다. 호출·사용자 허락의 진위는 프로그램이 증명하지 않습니다.')
f.n('start','메인: 최신 테스트 결과 준비','end').n('room','리뷰 회차가 남아 있는가?','decision').n('fresh','최신 테스트·로그가 유효하고\n진행 중 리뷰가 없는가?','decision').n('prepare','진행 중 리뷰 처리 또는\n필요한 수정·테스트 후 재준비').n('begin','review-begin → 회차 +1\n검증 대상 기록').n('call','verifier 읽기 전용 검증').n('returned','검증 결과를 받았는가?','decision').n('callfail','block → 호출 실패 보고\n회차는 이미 소비됨','pause').n('save','메인: review.md에 실제 응답 보존').n('clear','필수 근거가 충분하고\n판정 이견이 해소됐는가?','decision').n('unknown','wait / block\n부족한 근거·이견 보고','pause').n('defect','미해결 실제 결함 또는\n필수 기준 위반이 있는가?','decision').n('pass','review pass → ready\n선택적 개선은 후속 제안','end').n('fail','review fail 등록').n('remaining','리뷰 회차가 남아 있는가?','decision').n('fix','메인: 수정 → verify\n이전 지적·결과 갱신').n('wait','waiting → 자동 수정 중단\n문제·영향·시도·선택지 보고').n('permission','사용자가 추가 리뷰\n1회를 허락했는가?','decision').n('hold','미완료 보류','pause').n('extend','review-extend → 한도 +1\n기존 횟수·기록 유지')
for e in [('start','room',''),('room','fresh','예'),('room','wait','아니오'),('fresh','prepare','아니오'),('prepare','room','준비 후'),('fresh','begin','예'),('begin','call',''),('call','returned',''),('returned','callfail','아니오'),('returned','save','예'),('save','clear',''),('clear','unknown','아니오'),('clear','defect','예'),('defect','pass','아니오'),('defect','fail','예'),('fail','remaining',''),('remaining','fix','예'),('remaining','wait','아니오'),('fix','room','테스트 통과 후'),('wait','permission',''),('permission','hold','없음·보류'),('permission','extend','명시적 허락'),('extend','fix','')]:f.e(*e)

f=Flow('step-7-complete','7. 완료와 응답 종료','complete는 작업 완료를 기록합니다. Stop Hook은 응답 종료 시 확인하며 테스트를 대신 실행하거나 작업을 완료로 바꾸지 않습니다.')
f.n('start','메인: 진행 기록 정리 → complete','end').n('ready','ready 상태인가?','decision').n('valid','최신 코드·테스트·리뷰 증거가\n완료 조건을 충족하는가?','decision').n('reject','완료 거부 → 필요한 단계 재진행\n리뷰 한도면 사용자 결정 대기','pause').n('done','done 기록 → 결과·한계 보고').n('stop','응답 종료 시 Stop Hook').n('exempt','작업 없음 또는\n계획·대기·막힘 상태인가?','decision').n('check','done 상태이며\n최신 증거가 유효한가?','decision').n('loop','이미 Stop Hook 때문에\n이어진 응답인가?','decision').n('allow','응답 종료 허용\n기존 작업 상태 유지','end').n('warn','미완료 안내 후 응답 종료 허용\ndone으로 바꾸지 않음','end').n('block','한 번 종료 차단\n필요한 조치 안내').n('work','메인: 수정·재검증 또는\nwait / block 후 보고')
for e in [('start','ready',''),('ready','reject','아니오'),('ready','valid','예'),('valid','reject','아니오'),('valid','done','예'),('done','stop',''),('stop','exempt',''),('exempt','allow','예'),('exempt','check','아니오'),('check','allow','예'),('check','loop','아니오'),('loop','warn','예'),('loop','block','아니오'),('block','work',''),('work','stop','다시 응답 종료 시')]:f.e(*e)

# Keep the overview concise; the request classifier is expanded in step 2.
f=GRAPHS[0]
for key in ['question','reviewonly','improve']: del f.nodes[key]
f.nodes['kind']=('개발 요청인가?', 'decision')
f.n('other','질문·리뷰·하네스 개선\n요청별 경로로 → 2단계 참고','end')
f.edges=[e for e in f.edges if e[1] not in ['question','reviewonly','improve']]
f.edges=[(a,b,'예' if a=='kind' and b=='plan' else label) for a,b,label in f.edges]
f.e('kind','other','아니오')
# A sequence of yes/no decisions keeps request routing vertical and legible.
f=GRAPHS[2]
f.nodes['kind']=('단순 질문인가?', 'decision')
f.n('isreview','리뷰만 요청했는가?','decision').n('isharness','하네스 개선 요청인가?','decision')
f.edges=[e for e in f.edges if e[0]!='kind']
f.e('kind','q','예').e('kind','isreview','아니오').e('isreview','r','예').e('isreview','isharness','아니오').e('isharness','h','예').e('isharness','new','아니오: 개발')
# Stop can also run while the task is unfinished, not only after complete.
f=GRAPHS[7]
f.n('response','작업 도중 응답을 마치는 경우','end').e('response','stop','완료 여부와 무관하게')
MAIN_PATHS={
'workflow':['start','kind','plan','proceed','budget','build','test','tested','review','verdict','pass','complete','valid','done'],
'step-1-start':['start','load','enabled','call','state','active','next'],
'step-2-route':['start','kind','isreview','isharness','new','old','clear','resume'],
'step-3-plan':['start','new','create','write','choice','perf','budget','v','result','implement','go'],
'step-4-build':['start','room','ready','begin','edit','tool','path','phase','allowed','record','problem','more','done'],
'step-5-test':['start','room','config','before','normal','need','perf','target','record','stable','done'],
'step-6-review':['start','room','fresh','begin','call','returned','save','clear','defect','pass'],
'step-7-complete':['start','ready','valid','done','stop','exempt','check','allow']}

# Keep labels narrow without reducing the reading size or removing branches.
COMPACT_LABELS={
'workflow':{
'other':'질문·리뷰·하네스 개선\n2단계 참고','proceed':'계획 확정 +\n구현 요청 있음?',
'budget':'리뷰 회차가\n남았는가?','tested':'테스트 통과 +\n최신 증거?',
'complete':'complete\n상태·증거 확인','done':'done → 결과 보고\nStop Hook 확인',
'callfail':'block / wait\n호출·근거·이견 보고','extra':'review-extend\n한도 +1 · 횟수 유지'},
'step-3-plan':{'choice':'중요한 결정이\n남았는가?','perf':'기존 기능 개선 +\n성능 테스트 필요?',
'fail':'block / wait\n호출·근거·이견 보고','fix':'필수 누락 보완\n선택적 개선 제외'},
'step-4-build':{'room':'리뷰 회차가\n남았는가?','ready':'목표·범위·완료 기준\n작성 완료?',
'problem':'선택·환경\n문제가 있는가?','more':'남은 구현이\n있는가?'},
'step-5-test':{'room':'리뷰 회차가\n남았는가?','config':'단계·명령·환경\n실행 준비 완료?',
'stable':'코드 변경 없음 +\n필수 명령 모두 성공?', 'setup':'설정·환경 복구 / block\n준비 후 다시 시작'},
'step-6-review':{'room':'리뷰 회차가\n남았는가?','fresh':'최신 테스트·로그 유효 +\n진행 중 리뷰 없음?',
'clear':'필수 근거 충분 +\n판정 이견 해소?', 'defect':'미해결 결함 또는\n필수 기준 위반?',
'remaining':'리뷰 회차가\n남았는가?', 'permission':'추가 리뷰 1회\n사용자 허락?',
'wait':'waiting · 자동 수정 중단\n문제·시도·선택지 보고', 'prepare':'진행 중 리뷰 처리 /\n수정·테스트 후 재준비'},
'step-7-complete':{'valid':'최신 코드·테스트·리뷰\n완료 조건 충족?',
'reject':'완료 거부 → 재진행\n한도 도달 시 결정 대기','exempt':'작업 없음 /\n계획·대기·막힘?',
'check':'done +\n최신 증거 유효?', 'loop':'Stop Hook으로\n이어진 응답인가?',
'warn':'미완료 안내 후 종료\ndone 변경 없음','response':'작업 중 응답 종료',
'work':'수정·재검증 또는\nwait / block → 보고'}
}
for chart in GRAPHS:
    for key,label in COMPACT_LABELS.get(chart.name,{}).items():
        chart.nodes[key]=(label,chart.nodes[key][1])

def page(title,body,note=''):
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>body{{margin:0;background:#f1f3f1;color:#34483e;font:17px system-ui}}header{{padding:18px 28px;background:white;border-bottom:1px solid #dce3dd}}nav{{display:flex;gap:20px}}a{{color:#456554}}h1{{font-size:24px;font-weight:400}}main{{max-width:1350px;margin:24px auto;padding:0 18px}}.hint{{line-height:1.7;margin:18px 0}}.chart{{background:white;padding:16px;overflow:auto}}svg{{display:block;max-width:100%;height:auto;margin:auto}}section{{margin:35px 0}}img{{width:100%;height:auto}}button{{font:inherit;background:white;border:1px solid #bccbc0;border-radius:5px;padding:6px 12px;cursor:pointer}}.full svg{{max-width:none}}</style><header><nav><a href="index.html">모든 순서도</a><a href="workflow.html">전체 흐름</a><button onclick="document.querySelectorAll('.chart').forEach(x=>x.classList.toggle('full'))">원본 크기 / 화면 맞춤</button></nav></header><main><h1>{escape(title)}</h1><p class="hint">마름모: 조건 판단 · 사각형: 처리 · 둥근 모양: 시작·종료·대기<br>{escape(note)}</p>{body}</main></html>'''

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--viz-module',required=True);args=parser.parse_args()
    for f in GRAPHS:(ROOT/(f.name+'.dot')).write_text(f.dot())
    job=[{'name':f.name,'dot':f.dot()} for f in GRAPHS]
    render='''import {pathToFileURL} from 'node:url'; import fs from 'node:fs'; const {instance}=await import(pathToFileURL(process.argv[1])); const viz=await instance(); const data=JSON.parse(fs.readFileSync(0,'utf8')); for(const g of data){const svg=viz.renderString(g.dot,{format:'svg'}); fs.writeFileSync(process.argv[2]+'/'+g.name+'.svg',svg);}'''
    subprocess.run(['node','--input-type=module','-e',render,args.viz_module,str(ROOT)],input=json.dumps(job),text=True,check=True)
    for f in GRAPHS:
        from orthogonal_routes import reroute
        svg=reroute((ROOT/(f.name+'.svg')).read_text(), f, MAIN_PATHS[f.name])
        # Preserve original SVG scale for viewer zoom; use full-width in Markdown.
        svg=svg.replace('<svg ',f'<svg role="img" aria-label="{escape(f.title)}" ',1)
        (ROOT/(f.name+'.svg')).write_text(svg)
        (ROOT/(f.name+'.html')).write_text(page(f.title,'<div class="chart">'+svg+'</div>',f.note))
    gallery=''.join(f'<section><h1><a href="{f.name}.html">{escape(f.title)} — 확대 보기</a></h1><p class="hint">{escape(f.note)}</p><div class="chart">'+(ROOT/(f.name+'.svg')).read_text()+'</div></section>' for f in GRAPHS)
    (ROOT/'index.html').write_text(page('하네스 알고리즘 순서도',gallery))
    print('8 flowcharts: all decision branches labeled; SVG/HTML generated.')
