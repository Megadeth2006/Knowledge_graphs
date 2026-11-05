"""Rebuild ontology, two separate RDF graphs, validation and query results. Python 3.10+."""
from pathlib import Path
from decimal import Decimal
import csv,json,math
from itertools import combinations
from rdflib import Graph,Namespace,Literal,RDF,RDFS,OWL,XSD,BNode,URIRef
from rdflib.collection import Collection
from owlrl import DeductiveClosure,OWLRL_Semantics
from pyshacl import validate
P=Path(__file__).resolve().parent
EX=Namespace('https://example.org/rental-kg#')
PROV=Namespace('http://www.w3.org/ns/prov#')
classes={
    'Place':('Место расположения',None,'Адресный контейнер, в котором размещено жильё прямо или через цепочку locatedIn: здание, район или город. Жильё и объекты инфраструктуры в этот класс не входят.'),
    'Dwelling':('Жильё',None,'Жилая единица, предлагаемая в аренду снимком объявления через offers. В текущем проекте представлены целые квартиры; комнаты и отдельные дома могут быть добавлены при расширении модели.'),
    'Apartment':('Квартира','Dwelling','Отдельная жилая единица внутри здания, предлагаемая целиком в аренду; характеризуется площадью, числом комнат и этажом. Студия также является квартирой.'),
    'Building':('Здание','Place','Физическая постройка, содержащая жильё, с адресом и этажностью. Её точка WGS84 служит приближённой точкой расположения находящегося в ней жилья.'),
    'District':('Район','Place','Территориальная адресная единица внутри города, в которую входит здание. Членство определяется уровнем адресной иерархии, а не близостью к жилью.'),
    'City':('Город','Place','Населённый пункт, содержащий районы и являющийся верхним используемым уровнем адресной иерархии.'),
    'Amenity':('Объект инфраструктуры',None,'Объект обслуживания или отдыха, близость которого важна при подборе жилья. Является целью toAmenity и nearAmenity, а не адресным контейнером жилья. Объект обслуживания и вмещающее его здание моделируются отдельно.'),
    'MetroStation':('Станция метро','Amenity','Объект транспортного обслуживания, предоставляющий доступ к метрополитену. В текущем наборе представлен одной репрезентативной точкой, а не всеми входами.'),
    'Supermarket':('Супермаркет','Amenity','Объект розничного обслуживания для покупки продуктов и повседневных товаров. Обозначает магазин как объект обслуживания, а не его здание.'),
    'Park':('Парк','Amenity','Объект инфраструктуры для отдыха на озеленённой территории. В текущей модели расстояние измеряется до репрезентативной точки, а не до входа или границы парка.'),
    'ListingSnapshot':('Снимок объявления',None,'Зафиксированное на дату наблюдения предложение аренды одного жилья с ценой, условиями и ссылкой на запись источника; не является самим жильём.'),
    'DistanceMeasurement':('Измерение расстояния',None,'Результат измерения между одним жильём и одним объектом инфраструктуры, заданный числом метров, методом и датой расчёта. В текущем наборе используется точка здания жилья.'),
    'RenterProfile':('Профиль требований',None,'Набор явных требований арендатора к поиску: бюджет, город, радиус до метро и необходимость разрешения на животных. Не является человеком или объявлением.'),
    'SourceRecord':('Запись источника',None,'Указатель на исходную запись, из которой получены факты: URL реальной карточки либо явно учебный идентификатор источника. Используется как объект hasSource.'),
    'MeasurementMethod':('Метод измерения',None,'Описание способа получения численного расстояния, на которое ссылается usesMethod. Экземпляр Haversine обозначает расчёт по сферической модели Земли.')}
objects={
    'offers':('ListingSnapshot','Dwelling','предлагает жильё'),
    'hasListing':('Dwelling','ListingSnapshot','имеет объявление'),
    'inBuilding':('Dwelling','Building','находится в здании'),
    'inDistrict':('Building','District','находится в районе'),
    'inCity':('District','City','находится в городе'),
    'locatedIn':(None,'Place','расположено в'),
    'fromDwelling':('DistanceMeasurement','Dwelling','начало измерения'),
    'toAmenity':('DistanceMeasurement','Amenity','цель измерения'),
    'usesMethod':('DistanceMeasurement','MeasurementMethod','метод измерения'),
    'hasSource':(None,'SourceRecord','источник записи'),
    'preferredCity':('RenterProfile','City','предпочтительный город'),
    'nearAmenity':('Dwelling','Amenity','не далее 1000 м по прямой')}
attrs={
    'rentRubMonth':('ListingSnapshot','decimal','аренда, руб./месяц'),
    'utilitiesRubMonth':('ListingSnapshot','decimal','дополнительные КУ, руб./месяц'),
    'depositRub':('ListingSnapshot','decimal','залог, руб.'),
    'commissionRub':('ListingSnapshot','decimal','разовая комиссия, руб.'),
    'petsAllowed':('ListingSnapshot','boolean','животные разрешены'),
    'observedOn':('ListingSnapshot','date','дата наблюдения'),
    'term':('ListingSnapshot','string','категория срока аренды'),
    'minMonths':('ListingSnapshot','integer','минимальный срок, месяцев'),
    'areaM2':('Dwelling','decimal','площадь, м²'),'rooms':('Dwelling','integer','число комнат, студия = 0'),
    'floor':('Dwelling','integer','этаж'),'floorsTotal':('Building','integer','этажей в доме'),
    'address':('Building','string','адрес'),
    'latitude':(('Building','Amenity'),'decimal','широта, WGS84'),'longitude':(('Building','Amenity'),'decimal','долгота, WGS84'),
    'distanceM':('DistanceMeasurement','decimal','расстояние, м'),
    'calculatedOn':('DistanceMeasurement','date','дата вычисления'),
    'sourceUrl':('SourceRecord','anyURI','ссылка на источник'),
    'qualityNote':(None,'string','замечание к качеству'),
    'isSynthetic':(None,'boolean','синтетические данные'),
    'maxRentRubMonth':('RenterProfile','decimal','лимит арендной платы'),
    'maxMetroDistanceM':('RenterProfile','decimal','порог расстояния до метро'),
    'requiresPets':('RenterProfile','boolean','нужно разрешение на животных')}
def graph():
    g=Graph();g.bind('ex',EX);g.bind('prov',PROV);g.bind('owl',OWL);return g
ont=graph();ont.add((URIRef(str(EX).rstrip('#')),RDF.type,OWL.Ontology));ont.add((URIRef(str(EX).rstrip('#')),OWL.versionInfo,Literal('1.1.0')))
for n,(label,parent,definition) in classes.items():
    ont.add((EX[n],RDF.type,OWL.Class));ont.add((EX[n],RDFS.label,Literal(label,lang='ru')))
    ont.add((EX[n],RDFS.comment,Literal(definition,lang='ru')))
    if parent:ont.add((EX[n],RDFS.subClassOf,EX[parent]))
for n,(domain,ran,label) in objects.items():
    ont.add((EX[n],RDF.type,OWL.ObjectProperty));ont.add((EX[n],RDFS.range,EX[ran]));ont.add((EX[n],RDFS.label,Literal(label,lang='ru')))
    if domain:ont.add((EX[n],RDFS.domain,EX[domain]))
coordinate_domain=BNode();members=BNode()
ont.add((coordinate_domain,RDF.type,OWL.Class))
ont.add((coordinate_domain,OWL.unionOf,members));Collection(ont,members,[EX.Building,EX.Amenity])
for n,(domain,typ,label) in attrs.items():
    ont.add((EX[n],RDF.type,OWL.DatatypeProperty));ont.add((EX[n],RDFS.range,XSD[typ]));ont.add((EX[n],RDFS.label,Literal(label,lang='ru')))
    if domain:ont.add((EX[n],RDFS.domain,coordinate_domain if isinstance(domain,tuple) else EX[domain]))
ont.add((EX.offers,OWL.inverseOf,EX.hasListing));ont.add((EX.locatedIn,RDF.type,OWL.TransitiveProperty))
for p in ['inBuilding','inDistrict','inCity']:ont.add((EX[p],RDFS.subPropertyOf,EX.locatedIn))
disjoint_pairs=[('ListingSnapshot','Dwelling'),('DistanceMeasurement','Place')]
for group in [('Place','Dwelling','Amenity'),('City','District','Building'),('MetroStation','Supermarket','Park')]:
    disjoint_pairs.extend(combinations(group,2))
for a,b in disjoint_pairs:ont.add((EX[a],OWL.disjointWith,EX[b]))
ont.serialize(P/'ontology.ttl',format='turtle');ont.serialize(P/'ontology.owl',format='xml')

def lit(g,s,p,v):
    if v=='':return
    typ=attrs[p][1]
    if typ=='boolean': val=v if isinstance(v,bool) else v.lower()=='true'
    elif typ=='integer':val=int(v)
    elif typ=='decimal':val=Decimal(str(v))
    else:val=str(v)
    g.add((s,EX[p],Literal(val,datatype=XSD[typ])))
def read(name):return list(csv.DictReader((P/'data'/name).open(encoding='utf-8')))
def listing(g,r,synthetic):
    key=r['id']; l=EX[key];d=EX[key+'_home'];b=EX[key+'_building'];s=EX[key+'_source']
    for node,cls in [(l,'ListingSnapshot'),(d,'Apartment'),(b,'Building'),(s,'SourceRecord')]:g.add((node,RDF.type,EX[cls]));lit(g,node,'isSynthetic',synthetic)
    for a,p,o in [(l,'offers',d),(d,'inBuilding',b),(l,'hasSource',s),(d,'hasSource',s),(b,'hasSource',s)]:g.add((a,EX[p],o))
    for field,p in [('rent_rub_month','rentRubMonth'),('utilities_rub_month','utilitiesRubMonth'),('deposit_rub','depositRub'),('commission_rub','commissionRub'),('pets_allowed','petsAllowed'),('term','term'),('min_months','minMonths'),('quality_note','qualityNote')]:lit(g,l,p,r.get(field,''))
    lit(g,l,'observedOn','2026-10-22' if synthetic else r['observed_on'])
    for field,p in [('area_m2','areaM2'),('rooms','rooms'),('floor','floor')]:lit(g,d,p,r.get(field,''))
    for field,p in [('lat','latitude'),('lon','longitude')]:lit(g,b,p,r.get(field,''))
    lit(g,b,'floorsTotal',r['floors_total']);lit(g,b,'address',r.get('address','Учебный адрес '+key))
    lit(g,s,'sourceUrl',r.get('source_url','https://example.org/synthetic/'+key))
    return l,d,b
real=graph()
for r in read('observed_listings.csv'):listing(real,r,False)
real.serialize(P/'observed.ttl',format='turtle')
demo=graph();rows=read('demo_listings.csv');pois=read('demo_poi.csv')
for n,c in [('DemoCity','City'),('DemoDistrict','District'),('Haversine','MeasurementMethod'),('StudentWithPet','RenterProfile')]:demo.add((EX[n],RDF.type,EX[c]));lit(demo,EX[n],'isSynthetic',True)
demo.add((EX.DemoDistrict,EX.inCity,EX.DemoCity));demo.add((EX.StudentWithPet,EX.preferredCity,EX.DemoCity))
for p,v in [('maxRentRubMonth',40000),('maxMetroDistanceM',1000),('requiresPets',True)]:lit(demo,EX.StudentWithPet,p,v)
for r in pois:
    s=EX[r['id']];demo.add((s,RDF.type,EX[r['type']]));demo.add((s,RDFS.label,Literal(r['label'],lang='ru')))
    for p,v in [('latitude',r['lat']),('longitude',r['lon']),('isSynthetic',True)]:lit(demo,s,p,v)
def hav(a,b,c,d):
    a,b,c,d=map(math.radians,map(float,[a,b,c,d]));v=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return round(6371000*2*math.asin(min(1,math.sqrt(v))),1)
for r in rows:
    r['term']='long_term';l,d,b=listing(demo,r,True);demo.add((b,EX.inDistrict,EX.DemoDistrict))
    for po in pois:
        m=EX[r['id']+'_'+po['id']];dist=hav(r['lat'],r['lon'],po['lat'],po['lon']);demo.add((m,RDF.type,EX.DistanceMeasurement))
        for p,v in [('fromDwelling',d),('toAmenity',EX[po['id']]),('usesMethod',EX.Haversine)]:demo.add((m,EX[p],v))
        for p,v in [('distanceM',dist),('calculatedOn','2026-10-22'),('isSynthetic',True)]:lit(demo,m,p,v)
        if dist<=1000:demo.add((d,EX.nearAmenity,EX[po['id']]))
demo.serialize(P/'demo.ttl',format='turtle')
# OWL RL materialization: inference is separate from metric computations.
full=ont+demo;before=set(full);DeductiveClosure(OWLRL_Semantics).expand(full)
checks={'inverse':(EX.D01_home,EX.hasListing,EX.D01),'transitive':(EX.D01_home,EX.locatedIn,EX.DemoCity),'subclass':(EX.M1,RDF.type,EX.Amenity)}
for k,t in checks.items():assert t not in before and t in full,k
expected_homes={EX[r['id']+'_home'] for r in rows}
expected_places={EX[r['id']+'_building'] for r in rows}|{EX.DemoDistrict,EX.DemoCity}
expected_amenities={EX[r['id']] for r in pois}
for cls,expected in [('Place',expected_places),('Dwelling',expected_homes),('Amenity',expected_amenities)]:
    assert set(full.subjects(RDF.type,EX[cls]))==expected,cls
def disjoint_conflicts(g):
    return [(str(s),a,b) for a,b in disjoint_pairs
            for s in set(g.subjects(RDF.type,EX[a])) & set(g.subjects(RDF.type,EX[b]))]
assert not disjoint_conflicts(full),disjoint_conflicts(full)
assert not list(full.subjects(RDF.type,OWL.Nothing))
assert set(demo.subjects(EX.latitude,None))==expected_amenities|{EX[r['id']+'_building'] for r in rows}
assert set(demo.subjects(EX.longitude,None))==set(demo.subjects(EX.latitude,None))
inferred=graph()
for t in set(full)-before:inferred.add(t)
inferred.serialize(P/'results/inferred.ttl',format='turtle')
(ont+demo).serialize(P/'demo_with_ontology.owl',format='xml')
shapes=Graph().parse(P/'shapes.ttl')
report={}
for name,g in [('observed',real),('demo',demo)]:
    ok,rg,txt=validate(g,shacl_graph=shapes,ont_graph=ont,inference='rdfs');assert ok,txt
    (P/'results'/f'{name}_shacl.txt').write_text(txt);report[name]={'triples':len(g),'shacl_conforms':bool(ok)}
# Semantic regressions: domain/range must not conceal an incorrect endpoint.
negative_cases={
    'negative_rent':([],[(EX.D01,EX.rentRubMonth,Literal(-1,datatype=XSD.decimal))]),
    'amenity_as_building':([],[(EX.D01_home,EX.inBuilding,EX.M1)]),
    'amenity_as_location':([(EX.D01_home,EX.locatedIn,EX.S1)],[]),
    'dwelling_as_location':([(EX.D01_home,EX.locatedIn,EX.D02_home)],[]),
    'district_as_building':([],[(EX.D01_home,EX.inBuilding,EX.DemoDistrict)]),
    'building_as_city':([],[(EX.DemoDistrict,EX.inCity,EX.D01_building)]),
    'amenity_as_location_subject':([(EX.M1,EX.locatedIn,EX.DemoCity)],[]),
    'city_in_building':([(EX.DemoCity,EX.locatedIn,EX.D01_building)],[]),
    'district_in_building':([(EX.DemoDistrict,EX.locatedIn,EX.D01_building)],[]),
    'building_in_building':([(EX.D01_building,EX.locatedIn,EX.D02_building)],[]),
    'coordinates_on_dwelling':([(EX.D01_home,EX.latitude,Literal('59.9',datatype=XSD.decimal)),(EX.D01_home,EX.longitude,Literal('30.3',datatype=XSD.decimal))],[]),
    'coordinates_on_city':([(EX.DemoCity,EX.latitude,Literal('59.9',datatype=XSD.decimal)),(EX.DemoCity,EX.longitude,Literal('30.3',datatype=XSD.decimal))],[]),
    'latitude_out_of_range':([],[(EX.D01_building,EX.latitude,Literal(91,datatype=XSD.decimal))]),
    'incompatible_amenity_types':([(EX.M1,RDF.type,EX.Park)],[]),
    'incomplete_coordinate_pair':([],[]),
    'missing_building_height':([],[])}
negative_results={}
for name,(additions,replacements) in negative_cases.items():
    bad=graph();bad+=demo
    for t in additions:bad.add(t)
    for t in replacements:bad.set(t)
    if name=='incomplete_coordinate_pair':bad.remove((EX.D01_building,EX.longitude,None))
    if name=='missing_building_height':bad.remove((EX.D01_building,EX.floorsTotal,None))
    assert not validate(bad,shacl_graph=shapes,ont_graph=ont,inference='rdfs')[0],name
    negative_results[name]='PASS'
# The materialized graph also contains valid locatedIn assertions.
assert validate(full,shacl_graph=shapes,ont_graph=ont,inference='none')[0]
# A real snapshot must retain its source date instead of a hard-coded demo date.
date_row=dict(read('observed_listings.csv')[0],observed_on='2026-11-04')
date_graph=graph();date_listing,_,_=listing(date_graph,date_row,False)
assert date_graph.value(date_listing,EX.observedOn)==Literal('2026-11-04',datatype=XSD.date)
answers={}
for f in sorted((P/'queries').glob('*.rq')):
    g=real if f.name.startswith('real') else full
    res=g.query(f.read_text());answers[f.stem]=[{str(k):str(v) for k,v in row.asdict().items()} for row in res]
assert {r['listing'].split('#')[-1] for r in answers['demo_pet_budget']}=={'D06','D09','D12'}
assert {r['listing'].split('#')[-1] for r in answers['real_budget']}=={'R02'}
assert {r['home'] for r in answers['demo_inferred_city']}=={str(h) for h in expected_homes}
assert {r['listing'].split('#')[-1] for r in answers['demo_missing_pets']}=={'D02','D05','D08','D11'}
assert {r['listing'].split('#')[-1]:r['knownInitial'] for r in answers['real_initial_payment']}=={'R02':'33500','R04':'175000','R05':'120000','R06':'112500'}
assert {r['listing'].split('#')[-1] for r in answers['real_space_floor']}=={'R01','R04'}
assert {r['listing'].split('#')[-1] for r in answers['demo_shop_park']}=={'D04','D05','D06','D07','D09','D10','D11','D12'}
assert {r['listing']: (r['observedOn'],r['sourceUrl']) for r in answers['real_sources']}=={
    str(EX[r['id']]): (r['observed_on'],r['source_url']) for r in read('observed_listings.csv')}
assert len(answers)==8
report.update(classes=len(classes),object_properties=len(objects),datatype_properties=len(attrs),reasoning={k:'PASS' for k in checks},negative_validation_test='PASS',negative_validation_tests=negative_results,
    semantic_checks={'class_membership':'PASS','disjoint_types':'PASS','coordinate_ownership':'PASS','materialized_graph_shacl':'PASS','source_observation_date':'PASS'},
    inferred_instances={c:len(set(full.subjects(RDF.type,EX[c]))) for c in ['Place','Dwelling','Amenity']})
(P/'results/summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
(P/'results/query_results.json').write_text(json.dumps(answers,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
