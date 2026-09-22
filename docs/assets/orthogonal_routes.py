"""Route SVG edges on a rectilinear obstacle grid with exact cardinal anchors."""
import heapq
import re
import xml.etree.ElementTree as ET

NS='http://www.w3.org/2000/svg'
ET.register_namespace('',NS)
def tag(s):return '{'+NS+'}'+s

def reroute(svg, flow, main):
    root=ET.fromstring(svg); graph=root.find(tag('g')); nodes={}
    for g in graph.findall(tag('g')):
        if g.get('class')!='node':continue
        name=g.find(tag('title')).text
        ellipse=g.find(tag('ellipse'))
        if ellipse is not None:
            x,y,rx,ry=[float(ellipse.get(k)) for k in ('cx','cy','rx','ry')]; box=(x-rx,y-ry,x+rx,y+ry)
        else:
            polygon=g.find(tag('polygon'))
            if polygon is not None:
                pts=[tuple(map(float,p.split(','))) for p in polygon.get('points').split()]
            else:
                nums=list(map(float,re.findall(r'-?\d+(?:\.\d+)?',g.find(tag('path')).get('d'))));pts=list(zip(nums[::2],nums[1::2]))
            box=(min(x for x,y in pts),min(y for x,y in pts),max(x for x,y in pts),max(y for x,y in pts))
        nodes[name]=box
    def center(k):
        l,t,r,b=nodes[k];return ((l+r)/2,(t+b)/2)
    def anchor(k,side):
        l,t,r,b=nodes[k];x,y=center(k)
        return {'n':(x,t),'s':(x,b),'e':(r,y),'w':(l,y)}[side]
    def stub(p,s):
        dx,dy={'n':(0,-22),'s':(0,22),'e':(22,0),'w':(-22,0)}[s];return (p[0]+dx,p[1]+dy)
    obstacles=[(l-12,t-12,r+12,b+12) for l,t,r,b in nodes.values()]
    used=[]; labels=[]; allpoints=[]
    def clear(a,b):
        x,y=a;xx,yy=b
        for l,t,r,bot in obstacles:
            if x==xx and l<x<r and max(min(y,yy),t)<min(max(y,yy),bot):return False
            if y==yy and t<y<bot and max(min(x,xx),l)<min(max(x,xx),r):return False
        return True
    def route(start,end):
        xs={start[0],end[0]};ys={start[1],end[1]}
        for l,t,r,b in obstacles:xs.update((l,r));ys.update((t,b))
        xs.update((min(xs)-45,max(xs)+45));ys.update((min(ys)-45,max(ys)+45))
        xs=sorted(xs);ys=sorted(ys);src=(xs.index(start[0]),ys.index(start[1]));dst=(xs.index(end[0]),ys.index(end[1]))
        queue=[(0,0,src[0],src[1],-1)];dist={(src[0],src[1],-1):0};prev={};goal=None
        while queue:
            _,cost,i,j,d=heapq.heappop(queue);state=(i,j,d)
            if cost!=dist.get(state):continue
            if (i,j)==dst:goal=state;break
            a=(xs[i],ys[j])
            for ii,jj,nd in ((i-1,j,0),(i+1,j,0),(i,j-1,1),(i,j+1,1)):
                if not(0<=ii<len(xs) and 0<=jj<len(ys)):continue
                b=(xs[ii],ys[jj])
                if not clear(a,b):continue
                penalty=0
                for u,v in used:
                    if a[0]==b[0]==u[0]==v[0] and max(min(a[1],b[1]),min(u[1],v[1]))<min(max(a[1],b[1]),max(u[1],v[1])):penalty+=80
                    if a[1]==b[1]==u[1]==v[1] and max(min(a[0],b[0]),min(u[0],v[0]))<min(max(a[0],b[0]),max(u[0],v[0])):penalty+=80
                newcost=cost+abs(a[0]-b[0])+abs(a[1]-b[1])+(35 if d not in (-1,nd) else 0)+penalty
                nxt=(ii,jj,nd)
                if newcost<dist.get(nxt,float('inf')):
                    dist[nxt]=newcost;prev[nxt]=state
                    heapq.heappush(queue,(newcost+abs(b[0]-end[0])+abs(b[1]-end[1]),newcost,ii,jj,nd))
        assert goal is not None,(flow.name,start,end)
        path=[]
        while goal is not None:path.append((xs[goal[0]],ys[goal[1]]));goal=prev.get(goal)
        return path[::-1]
    groups={}
    for g in graph.findall(tag('g')):
        if g.get('class')=='edge':
            a,b=g.find(tag('title')).text.split('->');groups[(a.split(':')[0],b.split(':')[0])]=g
    for a,b,label in flow.edges:
        ax,ay=center(a);bx,by=center(b)
        if abs(ay-by)<1:tail,head=('e','w') if bx>ax else ('w','e')
        elif by<ay:tail=head='w' if ax<=bx else 'e'
        elif abs(ax-bx)<1:tail,head='s','n'
        else:tail,head=('e' if bx>ax else 'w'),'n'
        if flow.nodes[a][1]=='decision':
            outgoing=[target for source,target,_ in flow.edges if source==a]
            preferred=next((target for target in outgoing if (a,target) in list(zip(main,main[1:]))),None)
            if preferred is None:
                preferred=next((target for target in outgoing if center(target)[1]>ay+1),outgoing[0])
            alternatives=[target for target in outgoing if target!=preferred]
            tail='s' if b==preferred else ('e' if alternatives.index(b)==0 else 'w')
            if abs(ay-by)<1:head='w' if bx>ax else 'e'
        if by<ay:head='w'
        p,q=anchor(a,tail),anchor(b,head);st,en=stub(p,tail),stub(q,head)
        points=[p]+route(st,en)+[q]
        simple=[]
        for pt in points:
            if simple and pt==simple[-1]:continue
            while len(simple)>1 and ((simple[-2][0]==simple[-1][0]==pt[0]) or (simple[-2][1]==simple[-1][1]==pt[1])):simple.pop()
            simple.append(pt)
        assert all(u[0]==v[0] or u[1]==v[1] for u,v in zip(simple,simple[1:]))
        used.extend(zip(simple,simple[1:]));allpoints.extend(simple)
        g=groups[(a,b)]
        for child in list(g):g.remove(child)
        ET.SubElement(g,tag('title')).text=a+' → '+b
        ET.SubElement(g,tag('path'),{'d':'M '+' L '.join(f'{x:.2f},{y:.2f}' for x,y in simple),'fill':'none','stroke':'#71867e','stroke-width':'1.4','marker-end':'url(#flow-arrow)'})
        if label:
            width=sum(14 if ord(c)>127 else 7.5 for c in label)+12
            options=[]
            for u,v in zip(simple,simple[1:]):
                length=abs(u[0]-v[0])+abs(u[1]-v[1]);x=(u[0]+v[0])/2;y=(u[1]+v[1])/2
                if u[1]==v[1]:y-=11
                else:x+=width/2+7
                box=(x-width/2,y-13,x+width/2,y+7)
                collision=sum(not(box[2]<l or box[0]>r or box[3]<t or box[1]>bot) for l,t,r,bot in list(nodes.values())+labels)
                options.append((collision*10000+(0 if u[1]==v[1] and length>=width else 200)-length,x,y,box))
            _,x,y,box=min(options);labels.append(box)
            ET.SubElement(g,tag('text'),{'x':str(x),'y':str(y),'text-anchor':'middle','font-family':'Arial, sans-serif','font-size':'14','fill':'#4f625a','stroke':'white','stroke-width':'5','paint-order':'stroke','stroke-linejoin':'round'}).text=label
            allpoints.extend(((box[0],box[1]),(box[2],box[3])))
    defs=ET.SubElement(root,tag('defs'));marker=ET.SubElement(defs,tag('marker'),{'id':'flow-arrow','viewBox':'0 0 10 10','refX':'10','refY':'5','markerWidth':'7','markerHeight':'7','orient':'auto','markerUnits':'userSpaceOnUse'})
    ET.SubElement(marker,tag('path'),{'d':'M 0 0 L 10 5 L 0 10 Z','fill':'#71867e'})
    # Recompute the canvas from authored nodes, labels, and routed corridors.
    for l,t,r,b in nodes.values():allpoints.extend(((l,t),(r,b)))
    lo=min(x for x,y in allpoints)-30;top=min(y for x,y in allpoints)-30;hi=max(x for x,y in allpoints)+30;bot=max(y for x,y in allpoints)+30
    graph.set('transform',f'translate({-lo} {-top})');root.set('viewBox',f'0 0 {hi-lo} {bot-top}');root.set('width',str(hi-lo));root.set('height',str(bot-top))
    background=graph.find(tag('polygon'))
    if background is not None:graph.remove(background)
    # Paint the full SVG canvas so embedded images stay legible in dark mode.
    root.insert(0, ET.Element(tag('rect'), {'x':'0','y':'0','width':str(hi-lo),'height':str(bot-top),'fill':'#ffffff','aria-hidden':'true'}))
    return ET.tostring(root,encoding='unicode')
