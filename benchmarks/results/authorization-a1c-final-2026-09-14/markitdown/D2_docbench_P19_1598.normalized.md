Barack’s Wife Hillary:
Using Knowledge Graphs for Fact-Aware Language Modeling
RobertL.LoganIV∗ NelsonF.Liu†§ MatthewE.Peters§
MattGardner§ SameerSingh∗
∗UniversityofCalifornia,Irvine,CA,USA
†UniversityofWashington,Seattle,WA,USA
§AllenInstituteforArtificialIntelligence,Seattle,WA,USA
{rlogan, sameer}@uci.edu,{mattg, matthewp}@allenai.org,nfliu@cs.washington.edu
Abstract [SuperMarioLand]isa[1989][side-scrolling]
[platformvideogame]developedandpublished
Modelinghumanlanguagerequirestheability by[Nintendo]asa[launchtitle]fortheir[Game
to not only generate fluent text but also en- Boy][handheldgameconsole].
codefactualknowledge. However, traditional
publisher
Super Mario Land Nintendo launch game
language models are only capable of remem-
Q647249 Q8093 Q1425505
bering facts seen at training time, and often Publication platform manufacturer
genre
havedifficultyrecallingthem. Toaddressthis, Date
we introduce the knowledge graph language 21 April 1989 platform game Game Boy
Date Q828322 Q186437
model(KGLM),aneurallanguagemodelwith
instance of
mechanisms for selecting and copying facts
side-scrolling video game
from a knowledge graph that are relevant to Q2281714 handheld game console
Q941818
the context. These mechanisms enable the
modeltorenderinformationithasneverseen
Figure 1: Linked WikiText-2 Example. A localized
before, as well as generate out-of-vocabulary
knowledge graph containing facts that are (possibly)
tokens. WealsointroducetheLinkedWikiTextconveyedinthesentenceabove.Thegraphisbuiltbyit-
2dataset,1acorpusofannotatedtextalignedto
erativelylinkingeachdetectedentitytoWikidata,then
theWikidataknowledgegraphwhosecontents
adding any relations to previously mentioned entities.
(roughly)matchthepopularWikiText-2benchNotethatnotallentitiesareconnected,potentiallydue
mark(Merityetal.,2017). Inexperiments,we
tomissingrelationsinWikidata.
demonstrate that the KGLM achieves significantly better performance than a strong baseline language model. We additionally comtraining. Forinstance,whenconditionedonthetext
paredifferentlanguagemodels’abilitytocomat the top of Figure 1, an AWD-LSTM language
plete sentences requiring factual knowledge,
and show that the KGLM outperforms even model (Merity et al., 2018) trained on Wikitext-2
verylargelanguagemodelsingeneratingfacts. assigns higher probability to the word “PlayStation”than“GameBoy”,eventhoughthissentence
1 Introduction appears verbatim in the training data. This is not
surprising—existingmodelsrepresentthedistribuFor language models to generate plausible sention over the entire vocabulary directly, whether
tences,theymustbebothsyntacticallycoherentas
theyarecommonwords,referencestorealworld
wellasconsistentwiththeworldtheydescribe. Alentities,orfactualinformationlikedatesandnumthoughlanguagemodelsarequiteskilledatgenerat-
bers. As a result, language models are unable to
inggrammaticalsentences,andpreviousworkhas
generate factually correct sentences, do not genshownthatlanguagemodelsalsopossesssomede-
eralizetorare/unseenentities,andoftenomitrare
greeofcommon-sensereasoningandbasicknowltokensfromthevocabulary(insteadgeneratingUNedge (Vinyals and Le, 2015; Serban et al., 2016;
KNOWN tokens).
Trinh and Le, 2019), their ability to generate facWe introduce the knowledge graph language
tually correct text is quite limited. The clearest
model (KGLM), a neural language model with
limitationofexistinglanguagemodelsisthatthey,
mechanismsforselectingandcopyinginformation
atbest,canonlymemorizefactsobservedduring
from an external knowledge graph. The KGLM
1https://rloganiv.github.io/linked-wikitext-2 maintainsadynamicallygrowinglocalknowledge
5962
Proceedingsofthe57thAnnualMeetingoftheAssociationforComputationalLinguistics,pages5962–5971
Florence,Italy,July28-August2,2019.(cid:13)c2019AssociationforComputationalLinguistics

graph, a subset of the knowledge graph that con- 2 KnowledgeGraphLanguageModel
tainsentitiesthathavealreadybeenmentionedin
Inthissectionweintroducealanguagemodelthat

| thetext,andtheirrelatedentities. | | | | Whengenerating | | | | | | | | | |
| -------------------------------- | --- | --- | --- | -------------- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| entity tokens, | the | model | either | decides | to | render | | | | | | | |
| -------------- | --- | ----- | ------ | ------- | --- | ------ | --- | --- | --- | --- | --- | --- | --- |
| a new entity | that | is absent | | from the | local | graph, | | | | | | | |
| ------------ | ---- | --------- | --- | -------- | ----- | ------ | --- | --- | --- | --- | --- | --- | --- |
| render a | fact from | the | local | graph. | When | render- | | | | | | | |
| -------- | --------- | --- | ----- | ------ | ---- | ------- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | thesequenceoftokensobservedsofar. | | | | | Wedenote | |
| --- | --- | --- | --- | --- | --- | --- | --------------------------------- | --- | --- | --- | --- | -------- | --- |
| | | | | | | | t | | | | | | <t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
tially, the graph is empty and the model uses the i.e. languagemodelscomputep(x |x ). RNNlan-
| | | | | | | | | | | | t <t | | |
| ------------ | ----- | ---- | --- | ------ | --------- | ----- | --- | --- | --- | --- | ---- | --- | --- |
| entity Super | Mario | Land | to | render | the first | three | | | | | | | |
| knowledgegraph. | | Aftergeneratingthenexttwoto- | | | | | | | | | | | |
| --------------- | --- | ---------------------------- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
kens(“is”,“a”)usingthestandardlanguagemodel, p(x |x ) = softmax(W h +b),
| | | | | | | | | t | <t | | h t | | |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| themodelselectsSuper | | | Mario | Landastheparent | | | | | h = RNN(h | | ,x | ). | |
| -------------------- | --- | ---- | ----- | --------------- | --- | ------- | ------ | ----- | ----------- | --- | ---------------- | --- | --- |
| | | | | | | | | | t | | t−1 t−1 | | |
| entity, Publication | | Date | as | the relation | to | render, | | | | | | | |
| | | | | | | | We use | LSTMs | (Hochreiter | | and Schmidhuber, | | |
| | | | | | | | graphconsistingofentitiesE | | | | asnodes,withedges | | |
| -------- | --------- | ---------- | ------- | -------- | ------------ | --- | -------------------------- | ---- | -------- | --------- | ------------------ | ------- | --- |
| language | modeling, | we | collect | the | distantly | su- | | | | | | | |
| | | | | | | | defined | over | a set of | relations | R, | i.e. KG | = |
| pervised | Linked | WikiText-2 | | dataset. | The underly- | | | | | | | | |
| | | | | | | | {(p,r,e)|p | ∈ | E,r ∈ | R,e | ∈ E},wherepisapar- | | |
| | | | | | | | ententitywithrelationr | | | toanotherentitye. | | | Prac- |
| ------------- | ----------- | --------- | --- | ------------ | -------- | ------ | ------------------------ | ---- | ----- | ----------------- | ------------------ | ---- | ----- |
| 2017), a | popular | benchmark | | for language | | model- | | | | | | | |
| | | | | | | | tical KGs | have | other | aspects | that make | this | for- |
| ing, allowing | comparisons | | | against | existing | mod- | | | | | | | |
| | | | | | | | mulationsomewhatinexact: | | | | somerelationsareto | | |
| | | | | | | | literal values, | | such | as numbers | and | dates, | facts |
| -------------------- | --- | ----------------------- | --- | --- | --- | --- | --------------- | --------- | ---- | ---------- | ------------- | ------ | ----- |
| Wikidata(Vrandecˇic ́ | | andKrötzsch,2014)usinga | | | | | | | | | | | |
| | | | | | | | may be | expressed | as | properties | on relations, | | and |
| | | | | | | | entities | have aliases | | as the | set of strings | that | can |
| --------------------------------- | --- | --- | --- | --- | --------- | --- | -------- | ------------ | --- | ------ | -------------- | ----- | ------ |
| shelflinkingandcoreferencemodels. | | | | | Wealsouse | | | | | | | | |
| | | | | | | | refer to | the entity. | We | also | define a | local | knowl- |
| | | | | | | | edgegraphforasubsetofentitiesE | | | | | asKG | = |
| --- | --- | --- | --- | --- | --- | --- | ------------------------------ | --- | --- | --- | --- | ---- | --- |
| | | | | | | | | | | | <t | | <t |
| | | | | | | | {(p,r,e)|p | ∈ | E <t ,r | ∈ R,e | ∈ E}, i.e. | contains | |
| --------------- | --- | -------- | ------ | --- | ------- | ----- | ---------- | ----------------------------- | ------- | ----- | ---------- | -------- | --- |
| been mentioned: | | it could | either | be | related | to an | | | | | | | |
| | | | | | | | entitiesE | andallfactstheyparticipatein. | | | | | |
| entity that | is already | mentioned | | (including | | itself) | | | | | | | |
| ----------- | ---------- | --------- | --- | ---------- | --- | ------- | --- | --- | --- | --- | --- | --- | --- |
“Barackismarriedto ”)andshowthatKGLM Formally, we will compute p(x ,E |x ,E )
| | | | | | | | | | | | t | t <t | <t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---- | --- |
| | | | | | | | | <t | | | | | <t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | | | <t | | <t |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| theknowledgegraph. | | | | | | | describedabove. | | Thegenerativeprocessis: | | | | |
| ------------------ | --- | --- | --- | --- | --- | --- | --------------- | --- | ----------------------- | --- | --- | --- | --- |

Super Mario Land is a 1989 side-scrolling platform video game developed and published by N in tendo
parent from local entities
Super
platform game Mario Land Nintendo standard vocabulary
SELF PUBLISHER a
Relation to
side-scrolling game GENRE
the
Existing Entity Super Mario L
.
a
.
n
.
d PUB.DATE PLATFORM Game Boy
company
dog
pick from all entities pla g t a f m o e rm 1989 ...
AAA Inc.
aliases of et
Kabushiki
...
Mention of a Distribution over standard Koppai
New Entity Sony Inc. vocabulary and aliases of et
Nintendo
...
...
Zzyzx, CA
Not an Distribution over
Entity Mention standard vocabulary
Figure2:KGLMIllustration.Whentryingtogeneratethetokenfollowing“publishedby”,themodelfirstdecides
thetypeofthemention(tt)tobearelatedentity(darkerindicateshigherprobability),followedbyidentifyingthe
parent(pt),relation(rt),andentitytorender(et)fromthelocalknowledgegraphas(SuperMarioLand,Publisher,
Nintendo). ThefinaldistributionoverthewordsincludesthestandardvocabularyalongwithaliasesofNintendo,
andthemodelselects“Nintendo”asthetokenxt. FactsrelatedtoNintendowillbeaddedtothelocalgraph.
• Decide the type of x , which we denote by lect Nintendo as the entity to render (e ). When
t t
t : whether it is a reference to an entity in renderingNintendoasatokenx ,themodelhasan
t t
KG (related), areferencetoanentitynotin expanded vocabularyavailabletoit,containingthe
<t
KG (new),ornotanentitymention(∅). standard vocabulary along with all word types in
<t
• Ift = newthenchoosetheupcomingentitye anyofthealiasesofe .
t t t
fromthesetofallentitiesE.
Marginalizing out the KG There is a mismatch
• Ift = relatedthen:
t between our initial task requirement, p(x |x ),
t <t
– Chooseaparententityp fromE .
t <t andthemodelwedescribesofar,whichcomputes
– Chooseafactualrelationr torender,
t p(x ,E |x ,E ). We will essentially marginal-
t t <t <t
r ∈ {(p,r,e) ∈ KG |p = p }.
t <t t izeoutthelocalknowledgegraphtocomputethe
– Choosee asoneofthetailentities,
t probabilityofthetokens,i.e. p(x) = p(x,E).
E
e t ∈ {e|(p t ,r t ,e) ∈ KG <t }. Wewillclarifythis,alongwithdescrib P ingthetrain-
• Ift = ∅thene = ∅.
t t ingandtheinference/decodingalgorithmsforthis
• Generatex conditionedone ,potentiallycopy-
t t modelandotherdetailsofthesetup,inSection4.
ingoneofe ’saliases.
t
• Ife ∈/ E ,thenE ← E ∪{e },
t <t <(t+1) <t t 2.3 ParameterizingtheDistributions
elseE ← E .
<(t+1) <t
Theparametricdistributionsusedinthegenerative
For the model to refer to an entity it has already
mentioned, we introduce a Reflexive relation that process above are defined as follows. We begin
self-relates,i.e. p = efor(p,Reflexive,e). by computing the hidden state h t using the formula in Eqn (1). We then split the vector into
Anillustrationofthisprocessandthevariables
three components: h = [h ;h ;h ], which
is provided in Figure 2, for generating a token in t t,x t,p t,r
arerespectivelyusedtopredictwords,parents,and
the middle of the same sentence as in Figure 1.
relations. The type of the token, t , is computed
Amongst the three mention types (t ), the model t
t using a single-layer softmax over h to predict
chooses a reference to existing entity, which re- t,x
oneof{new,related,∅}.
quirespickingafacttorender. Astheparententity
ofthisfact(p ),themodelpicksSuperMarioLand, Picking an Entity We also introduce pretrained
t
and then follows the Publisher relation (r ) to se- embeddings for all entities and relations in the
t
5964

knowledge graph, denoted by v for entity e and graph,thetextismadeupofdisjointsentencesthat
e
v for relation r. To select e from all entities in do not provide sufficient context to train a pow-
r t
caset = new,weuse: erful language model. Our goals are much more
t
aligned to the data-to-text task (Ahn et al., 2016;
p(e t ) = softmax(v e ·(h t,p +h t,r )) Lebret et al., 2016; Wiseman et al., 2017; Yang
et al., 2017; Gardent et al., 2017; Ferreira et al.,
overalle ∈ E. Thereasonweaddh andh is
t,p t,r 2018),whereasmalltable-sizedKBisprovidedto
tomimicthestructureofTransE,whichweuseto
generateashortpieceoftext;weareinterestedin
obtainentityandrelationembeddings. Detailson
languagemodelsthatdynamicallydecidethefacts
TransEwillbeprovidedinSection4. Formention
toincorporatefromtheknowledgegraph,guided
of a related entity, t = related, we pick a parent
t bythediscourse.
entityp using
t
For these reasons we introduce the Linked
p(p ) = softmax(v ·h ) WikiText-2 dataset, consisting of (approximately)
t p t,p
the same articles appearing in the WikiText-2 lanoverallp ∈ E t ,thenpicktherelationr t using guage modeling corpus, but linked to the Wikidata (Vrandecˇic ́ and Krötzsch, 2014) knowledge
p(r ) = softmax(v ·h )
t r t,r graph. Because the text closely matches, modelstrainedonLinkedWikiText-2canbecompared
over all r ∈ {r|(p ,r,e) ∈ KG }. The combina-
t t
to models trained on WikiText-2. Furthermore,
tion of p and r determine the entity e (which
t t t
because many of the facts in Wikidata are demust satisfy (p ,r ,e ) ∈ KG ; if there are multi-
t t t t
rivedfromWikipediaarticles,theknowledgegraph
pleoptionsoneischosenatrandom).
has a good coverage of facts expressed in the
Rendering the Entity If e = ∅, i.e. there is
t text. The dataset is available for download at:
no entity to render, we use the same distribution
https://rloganiv.github.io/linked-wikitext-2. Our
overthevocabularyasinEqn(1)-asoftmaxusing
systemannotatesonedocumentatatime,andcon-
h . If there is an entity to render, we construct
t,x sists of entity linking, relation annotations, and
the distribution over the original vocabulary and
post-processing. The following paragraphs deavocabularycontainingallthetokensthatappear
scribeeachstepindetail.
in aliases of e . This distribution is conditioned
t
Initial entity annotations We begin by identifyon e in addition to x . To compute the scores
t t
over the original vocabulary, h is replaced by inganinitialsetofentitymentionswithinthetext.
t,x
h′ = W [h ;v ] where W is a learned Theprimarysourceofthesementionsisthehuman-
t,x proj t,x et proj
providedlinksbetweenWikipediaarticles. Whenweightmatrixthatprojectstheconcatenatedvector
intothesamevectorspaceash . everaspanoftextislinkedtoanotherWikipedia
t,x
article, we associate its corresponding Wikidata
To obtain probabilities for words in the alias
entitywiththespan. Whilearticlelinksprovidea
vocabulary, we use a copy mechanism Gu et al.
largenumberofgoldentityannotations,theyarein-
(2016). Thetokensequencescomprisingeachalias
sufficientforcapturingallofthementionsinthear-
{a }areembeddedthenencodedusinganLSTM
j
toformvectorsa . Copyscoresarecomputedas: ticlesinceentitiesareonlylinkedthefirsttimethey
j
occur. Accordingly, we use the neural-el (Gupta
p(x = a ) ∝ exp σ h′ T W a etal.,2017)entitylinkertoidentifyadditionallinks
t j h (cid:16) t,x copy(cid:17) ji
(cid:0) (cid:1) toWikidata,andidentifycoreferencesusingStanfordCoreNLP2 tocoverpronouns,nominals,and
othertokensmissedbythelinker.
3 LinkedWikiText-2
Localknowledgegraph Thenextstepiteratively
Modelingaside,oneoftheprimarybarrierstoin- createsagenerativestoryfortheentitiesusingrelacorporatingfactualknowledgeintolanguagemod- tionsintheknowledgegraphaswellasidentifies
elsisthattrainingdataishardtoobtain. Standard newentities. Todothis,weprocessthetexttoken
language modeling corpora consist only of text, bytoken. Eachtimeanentityisencountered,we
and thus are unable to describe which entities or addalloftherelatedentitiesinWikidataascandifactseachtokenisreferringto. Incontrast,while
relationextractiondatasetslinktexttoaknowledge 2https://stanfordnlp.github.io/CoreNLP/
5965

Tokens xt Super Mario Land is a 1989 side - scrolling platform video game developed
Mentiontype tt new ∅ ∅ related new related ∅
EntityMentioned et SML ∅ ∅ 04-21-1989 SIDE_SCROLL PVG ∅
Relation rt ∅ ∅ ∅ pub date ∅ genre ∅
ParentEntity pt ∅ ∅ ∅ SML ∅ SML ∅
xt and published by Nintendo as a launch title for their Game Boy handheld game console .
tt ∅ ∅ ∅ related ∅ ∅ new ∅ ∅ related related ∅
et ∅ ∅ ∅ NIN ∅ ∅ LT ∅ ∅ GAME_BOY HGC ∅
rt ∅ ∅ ∅ pub ∅ ∅ ∅ ∅ ∅ R:manu / platform instance of ∅
pt ∅ ∅ ∅ SML ∅ ∅ ∅ ∅ ∅ NIN / SML GAME_BOY ∅
Table 1: Example Annotation of the sentence from Figure 1, including corresponding variables from Figure 2.
Note that Game Boy has multiple parent and relation annotations, as the platform for Super Mario Land and as
manufacturedbyNintendo. Wikidataidentifiersaremadehuman-readable(e.g.,SMLisQ647249)forclarity.
datesformatching. Ifoneoftheserelatedentities Train Dev Test
isseenlaterinthedocument,weidentifytheentity
Documents 600 60 60
as a parent for the later entity. Since multiple re- Tokens 2,019,195 207,982 236,062
Vocab.Size 33,558 - -
lationsmayappearasexplanationsforeachtoken,
MentionTokens 207,803 21,226 24,441
weallowatokentohavemultiplefacts. MentionSpans 122,983 12,214 15,007
UniqueEntities 41,058 5,415 5,625
Expanding the annotations Since there may be
UniqueRelations 1,291 484 504
entitiesthatweremissedintheinitialset,aswell
asnon-entitytokensofinterestsuchasdatesand Table2: LinkedWikiText-2CorpusStatistics.
quantitieswefurtherexpandtheentityannotations
usingstringmatching. Forentities,wematchthe
Evenwiththeseomissionsandmistakes,itisclear
setofaliasesprovidedinWikidata. Fordates,we
that the annotations are rich and detailed, with a
createanexhaustivelistofallofthepossibleways
highcoverage,andthusshouldprovebeneficialfor
of expressing the date (e.g. "December 7, 1941",
trainingknowledgegraphlanguagemodels.
"7-12-1941", "1941", ...). We perform a similar
approach for quantities, using the pint library in DatasetStatistics StatisticsforLinkedWikiText-2
Pythontohandlethedifferentwaysofexpressing areprovidedinTable2. Inthiscorpus,morethan
units(e.g. "g","gram",...). Sincetherearemany 10%ofthetokensareconsideredentitytokens,i.e.
waystoexpressanumericalquantity,weonlyren- theyaregeneratedasfactualreferencestoinformaderthequantityatthelevelofprecisionsupplied tion in the knowledge graph. Each entity is only
byWikidata,anddonotperformunitconversions. mentionedafewtimes(lessthan5onaverage,with
alongtail),andwithmorethanthousanddifferent
Example Annotation An example annotation is
relations. Thus it is clear that regular language
providedinTable1correspondingtotheinstancein
modelswouldnotbeabletogeneratefactualtext,
Figure1,alongwiththevariablesthatcorrespond
andthereisaneedforlanguagemodelstobeable
tothegenerativeprocessoftheknowledgegraph
torefertoexternalsourcesofinformation.
languagemodel(KGLM).Theentitymentionedfor
mosttokensherearehuman-providedlinks,apart Differences from WikiText-2 Although our
from “1989” that is linked to 04-21-1989 by the datasetisdesignedtocloselyreplicateWikiText-2,
stringmatchingprocess. Theannotationsindicate therearesomedifferencesthatpreventdirectcomwhichoftheentitiesarenewandrelated basedon parison. Firstly,thereareminorvariationsintext
whethertheyarereachablebyentitieslinkedsofar, acrossarticlesduetoeditsbetweendownloaddates.
clearly making a mistake for side-scrolling game Secondly,accordingtocorrespondencewithMerity
and platform video game due to missing links in etal.(2017),WikiText-2wascollectedbyquerying
Wikidata. Finally, multiple plausible reasons for theWikipediaTextAPI.BecausethisAPIdiscards
Game Boyareincluded: it’stheplatformforSuper useful annotation information (e.g. article links),
Mario Land and it is manufactured by Nintendo, LinkedWikiText-2insteadwascreatedbydirectly
eventhoughonlytheformerismorerelevanthere. fromthearticleHTML.
5966

4 TrainingandInferenceforKGLM ThisapproachisusedtoevaluatemodelsinJietal.

| | | | | | | | (2017) | and Dyer | et al. (2016). | | Following | Ji | et al. |
| --- | --- | --- | --- | --- | --- | --- | ------ | -------- | -------------- | --- | --------- | --- | ------ |
(2017),wecomputeq(E|x)usingadiscriminative
| PretrainedKGEmbeddings | | | | | Duringevaluation, | | | | | | | | |
| ---------------------- | --- | --- | --- | --- | ----------------- | --- | --- | --- | --- | --- | --- | --- | --- |
| we | may need | to | make predictions | | on | entities and | | | | | | | |
| --------- | -------- | --------- | ---------------- | ---- | ------ | ------------ | ------------- | --- | --- | --- | --- | --- | --- |
| relations | | that have | not been | seen | during | training. | 5 Experiments | | | | | | |
| | | | | | | | To evaluate | | the proposed | language | | model, | we |
| ----- | --- | --------- | ----- | -------- | --- | --------- | ----------- | --- | ------------ | -------- | --- | ------ | --- |
| 2013) | on | Wikidata. | Given | (p,r,e), | we | learn em- | | | | | | | |
| | | p r | e | | | | tionusingperplexityofheld-outcorpus,accuracy | | | | | | |
| --- | --- | --- | --- | --- | --- | --- | -------------------------------------------- | --- | --- | --- | --- | --- | --- |
| | | p r | e ) = | p | r | e | | | | | | | |
| --- | --- | --- | ----- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| L | = max | 0,γ +δ(v | ,v | ,v | )−δ | v′,v ,v′ | | | | | | | |
| --- | ----- | -------- | --- | --- | --- | -------- | --- | --- | --- | --- | --- | --- | --- |
| | | | p | r e | | p r e | | | | | | | |
| | | | | | | | • AWD-LSTM | | (Merity | et al., | 2018): | | strong |
| --- | --- | --- | --- | --- | --- | --- | ---------- | --- | ------- | ------- | ------ | --- | ------ |
| | | | | | | | LSTM-based | | model used | as | the foundation | | of |
| -------- | --- | ----------- | ---------- | --- | -------- | --- | ---------- | --- | ---------- | --- | -------------- | --- | --- |
| Training | | with Linked | WikiText-2 | | Although | the | | | | | | | |
| | | | | | | | • | | (Jietal.,2017): | | anLSTM-based | | |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --------------- | --- | ------------ | --- | --- |
| | | | | | | | language | model | with the | ability | to | track | entity |
| -------- | --- | -------- | --------- | --- | ------------ | ---- | --------- | ---------------------------------- | -------- | ------- | --- | ----- | ------ |
| forward. | | Our loss | objective | is | the negative | log- | | | | | | | |
| | | | | | | | mentions. | Embeddingsforentitiesarecreateddy- | | | | | |
| | l(Θ) | = | logp(x | ,E |x | ,E | ;Θ), | | | | | | | |
| --- | ---- | --- | ------ | ----- | ----- | ---- | ---------------- | --- | ---------------------- | --- | --- | --- | --- |
| | | | | t t | <t <t | | | | | | | | |
| | | X | | | | | • EntityCopyNet: | | avariantoftheKGLMwhere | | | | |
| | | | | | | | t = | new for | all mentions, | | i.e. | entities | are |
| --- | --- | --- | --- | --- | --- | --- | --- | ------- | ------------- | --- | ---- | -------- | --- |
| | | | | | | | Hyperparameters | | Wepre-train256dimensional | | | | |
| --- | --- | --- | --- | --- | --- | --- | --------------- | --- | ------------------------- | --- | --- | --- | --- |
| | | | | | | | entity and | relation | embeddings | | for | all entities | |
| --- | --- | --- | --- | --- | --- | --- | ---------- | -------- | ---------- | --- | --- | ------------ | --- |
| Inference | | Whileobservingannotationsmakesthe | | | | | | | | | | | |
| --------- | --- | --------------------------------- | --- | --- | --- | --- | -------------------------------------- | --- | --- | --- | --- | --- | ---- |
| | | | | | | | LinkedWikiText-2usingTransEwithmarginγ | | | | | | = 1. |
| probabilityp(x) | | | p(x,E)notthejointproba- | | | | | | | | | | |
| --------------- | --- | --- | ----------------------- | --- | --- | --- | ---------------------------------- | --- | --- | --- | --- | ------ | --- |
| | | | = E | | | | hiddendimension1150toencodetokens. | | | | | Wealso | |
| due | to the | large | combinatorial | | space | of possible | | | | | | | |
| --- | ------ | ----- | ------------- | --- | ----- | ----------- | --- | --- | --- | --- | --- | --- | --- |
| sampling. | | Givensamplesfromaproposaldistribu- | | | | | | | | | | | |
| --------- | --- | ---------------------------------- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
tionq(E|x)themarginaldistributionis:
| | p(x) | p(x,E) | | | | q(E|x) | 5.2 Results | | | | | | |
| --- | ---- | ------ | ------ | --- | ------ | ------ | ----------- | ------------------------------- | --- | --- | --- | --- | --- |
| | = | | = | | | | | | | | | | |
| | | X | | X | q(E|x) | | | | | | | | |
| | | E | | E | | | | | | | | | |
| | | | | | | | Perplexity | Weevaluateourmodelusingthestan- | | | | | |
| | | 1 | p(x,E) | | | | | | | | | | |
| | ≈ | | | | | | dard perplexity | | metric: | exp | | logp(x | ) . |
| --- | --- | --- | ------ | --- | --- | --- | --------------- | --- | ------- | --------- | --- | ------ | ---------- |
| | | N X | q(E|x) | | | | | | | (cid:16)T | t=1 | | t (cid:17) |
| | | E∼q | | | | | | | | | P | | |

| | | | | | PPL | UPP | | AWD- | | | KGLM | |
| --- | --- | --- | --- | --- | --- | --- | --- | ---- | --- | --- | ---- | --- |
| | | | | | | | | LSTM | | Oracle | | NEL |
| --- | --- | --- | --- | --- | --- | --- | --- | ---- | --- | ------ | --- | --- |
| | | | | | 85.4 | 189.2 | | | | | | |
| -------------------------- | --- | --- | --- | --- | ---- | ----- | -------------- | ---- | ----- | ----- | --- | ----- |
| EntityCopyNet* | | | | | | | nation-capital | 0/0 | 6/7 | 0/0 | | 0/4 |
| | | | | | 76.1 | 144.0 | | | | | | |
| | | | | | | | birthloc | 0/9 | 14/14 | 94/95 | | 85/92 |
| AWD-LSTM(Merityetal.,2018) | | | | | 74.8 | 165.8 | | | | | | |
| | | | | | | | birthdate | 0/25 | 8/9 | 65/68 | | 61/67 |
| KGLM* | | | | | 44.1 | 88.5 | | | | | | |
| | | | | | | | spouse | 0/0 | 2/3 | 2/2 | | 1/19 |
| | | | | | | | city-state | 0/13 | 62/62 | 9/59 | | 4/59 |
| | | | | | | | Average | 0.0/8.2 | 15.3/15.8 | 38.5/47.7 | 29.3/44.8 | |
| --- | --- | --- | --- | --- | --- | --- | ------- | ------- | --------- | --------- | --------- | --- |
| | | | | | | | Table 4: | Fact Completion. | | Top-k | | accuracy |
| --- | --- | --- | --- | --- | --- | --- | -------- | ---------------- | --- | ----- | --- | -------- |
| | | | | | | | pletefactualsentence. | SeeexamplesinTable5. | | | | |
| ------ | ---- | ---- | ---------- | --- | ----------- | --- | --------------------- | -------------------- | --- | --- | --- | --- |
| tokens | when | they | are mapped | | to a single | UNK | | | | | | |
| | | | | | | | of (X,Y) | pairs for which | | the relation | holds, | and |
| --- | --- | --- | --- | --- | --- | --- | -------- | --------------- | --- | ------------ | ------ | --- |
| | | | | | | | modelontherelations. | | TheoracleKGLMisgiven | | | |
| --- | --- | --- | --- | --- | --- | --- | -------------------- | --- | -------------------- | --- | --- | --- |
| introduced | by | Ueberla | (1994), | | and used | recently | | | | | | |
| ---------- | --- | ------- | ------- | --- | -------- | -------- | --- | --- | --- | --- | --- | --- |
| | | | | | | | KGLM | variants significantly | | outperform | | AWD- |
| ------- | ---- | ------ | --------- | --- | --------------- | --- | ---- | ---------------------- | --- | ---------- | --- | ---- |
| (2018). | This | metric | penalizes | | the probability | of | | | | | | |
| UNK tokens | | by evenly | dividing | | their | probability | | | | | | |
| ---------- | --- | --------- | --------- | --- | -------- | ----------- | -------------------------------- | --- | --- | --- | --- | ----- |
| | | | | | | | LSTMproducedgeneric,commonwords. | | | | | KGLMs |
| mass over | U, | the set | of tokens | | that get | mapped | | | | | | |
| | | | | | | | of magnitude | more data, | producing | | factual | com- |
| ------ | ------ | ---------- | --- | ----- | --- | --------- | ------------ | ---------- | --------- | --- | ------- | ---- |
| p(UNK) | in the | perplexity | | above | by | 1 p(UNK), | | | | | | |
|U|
| | | | | | | | pletions | that require | specific | knowledge, | | such as |
| --- | --- | --- | --- | --- | --- | --- | -------- | ------------ | -------- | ---------- | --- | ------- |
where|U|isestimatedfromthedata.
| | | | | | | | birthplaces,dates,andauthors. | | | However,theydo | | |
| -------------------------------------- | --- | --- | --- | --- | --- | --- | ----------------------------- | --- | --- | -------------- | --- | --- |
| WepresentthemodelperplexitiesinTable3. | | | | | | To | | | | | | |
| | | | | | | | inlargecorpora,likethecitieswithinstates.3 | | | | | Itis |
| --- | --- | --- | --- | --- | --- | --- | ------------------------------------------ | --- | --- | --- | --- | ---- |
| timated | using | the importance | | sampling | | approach | | | | | | |
| ------- | ----- | -------------- | --- | -------- | --- | -------- | --- | --- | --- | --- | --- | --- |
| describedinSection4. | | | WeobservethattheKGLM | | | | | | | | | |
| -------------------- | --- | --- | -------------------- | --- | --- | --- | ---------- | -------- | --- | ----- | -------------- | --- |
| | | | | | | | We provide | examples | in | Table | 5 to highlight | |
| entity-basedlanguagemodels(44.1vs. | | | | | | 76.1/85.4), | | | | | | |
| ---------------------------------- | --- | --- | --- | --- | --- | ----------- | --- | --- | --- | --- | --- | --- |
| providing | strong | evidence | | that | leveraging | knowl- | | | | | | |
| --------- | ------ | -------- | --- | ---- | ---------- | ------ | --- | --- | --- | --- | --- | --- |
| eling. | Furthermore, | | KGLM | significantly | | outper- | | | | | | |
| ------ | ------------ | --- | ---- | ------------- | --- | ------- | ------ | ------------ | ---- | ----------- | --- | ------ |
| | | | | | | | 2019). | For examples | that | both models | get | factu- |
| | | | | | | | ally correct | or incorrect, | the | generated | tokens | by |
| --- | --- | --- | --- | --- | --- | --- | ------------ | ------------- | --- | --------- | ------ | --- |
| | | | | | | | for popular | entities). | KGLM, | in particular, | | gets |
| ----------- | --- | ------ | ----------- | --- | --------- | ---- | ----------- | ---------- | ----- | -------------- | --- | ---- |
| of language | | models | to complete | | sentences | with | | | | | | |
| modeltrainedonamuchlargercorpusoftext. | | | | | | We | | | | | | |
| -------------------------------------- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

| | | InputSentence | | | | Gold | GPT-2 | KGLM | |
| --- | --- | -------------------- | --- | --- | --- | ------------- | ----- | ---- | --- |
| | | ParisHiltonwasbornin | | | | New York City | New | 1981 | |
| | | ArnoldSchwarzeneggerwasbornon | | | | 1947-07-30 | July | 30 | |
| --- | ------------ | ------------------------------ | --- | --- | --- | -------------- | -------- | ------ | --- |
| | | BobDylanwasbornin | | | | Duluth | New | Duluth | |
| | KGLMcorrect | BarackObamawasbornon | | | | 1961-08-04 | January | August | |
| | | Ulyssesisabookthatwaswrittenby | | | | James Joyce | a | James | |
| | | St.Louisisacityinthestateof | | | | Missouri | Missouri | Oldham | |
| | GPTv2correct | RichardNixonwasbornon | | | | 1913-01-09 | January | 20 | |
| | | KanyeWestismarriedto | | | | Kim Kardashian | Kim | the | |
| | | ThecapitalofIndiais | | | | New Delhi | the | a | |
| | | Madonnaismarriedto | | | | Carlos Leon | a | Alex | |
| --- | --- | ------------------ | --- | --- | --- | ----------- | --- | ---- | --- |
| | | | | | Data-to-textgeneration | | Ourworkisalsorelated | | |
| ---------------------- | ---------- | --------------------- | ------- | ------ | ---------------------- | -------------- | -------------------- | ----------- | --- |
| pletion | of “Barack | Obama was | born on | ” with | | | | | |
| | | | | | to the | task of neural | data-to-text | generation. | For |
| theoriginalfact(Barack | | Obama,birthDate,1961- | | | | | | | |
| 08-04), | resulting | in the top | three decoded | tokens | | | | | |
| ------- | --------- | ---------- | ------------- | ------ | --- | --- | --- | --- | --- |
| as“August”,“4”,“1961”. | | Afterchangingthebirth | | | | | | | |
| ---------------------- | --- | --------------------- | --- | --- | --- | --- | --- | --- | --- |
| date | to 2013-03-21, | the top | three decoded | tokens | | | | | |
| ---- | -------------- | ------- | ------------- | ------ | --- | --- | --- | --- | --- |
| become | “March”, | “21”, “2013”. | Thus, changing | | | | | | |
| ------ | -------- | ------------- | -------------- | --- | --- | --- | --- | --- | --- |
| | | | | | Wikipediainfo-boxes(Lebretetal.,2016). | | | | Thepri- |
| --- | --- | --- | --- | --- | -------------------------------------- | --- | --- | --- | ------- |
| | | | | | ourmotivation. | Theseworksfocusongenerating | | | |
| --- | --- | --- | --- | --- | -------------- | --------------------------- | --- | --- | --- |
| | | | | | coherenttextwithinanarrowdomain(e.g. | | | | sports, |
| --- | --- | --- | --- | --- | ------------------------------------ | --- | --- | --- | ------- |
| Knowledge-based | | language | models Our | work | | | | | |
| --------------- | ----------- | -------- | ------------------- | ---- | ----------------------------- | --- | --- | --- | -------- |
| | | | | | ricssuchasBLEUandMETEORscore. | | | | Ourfocus |
| draws | inspiration | from two | existing knowledge- | | | | | | |
| (i) | ENTITYNLM | (Ji et | al., 2017) which | im- | | | | | |
| --- | --------- | ------ | ---------------- | --- | --- | --- | --- | --- | --- |
| | | | | | 2 (Gong | et al., 2018; | Yang et | al., 2018; | Krause |
| --- | --- | --- | --- | --- | ------- | ------------- | ------- | ---------- | ------ |

| 7 ConclusionsandFutureWork | | | | | | References | | | | | |
| -------------------------- | --- | --- | --- | --- | --- | ---------- | --- | --- | --- | --- | --- |
| | | | | | | YoshuaBengio.2016. | | Aneuralknowledgelanguage | | | |
| --- | --- | --- | --- | --- | --- | ------------------ | --- | ------------------------ | --- | --- | --- |
| | | | | | | model. | ArXiv:1608.00318. | | | | |
| ---------------- | --- | --------- | -------------- | --- | -------- | ------ | ----------------- | --- | --- | --- | --- |
| about real-world | | entities. | In particular, | | they are | | | | | | |
| | | | | | | Antoine | Bordes, | Nicolas | Usunier, | Alberto | Garcia- |
| --- | --- | --- | --- | --- | --- | ------- | ------- | ------- | -------- | ------- | ------- |
| | | | | | | 2013. | Translating | embeddings | | for modeling | multi- |
| --- | --- | --- | --- | --- | --- | ----- | ----------- | ---------- | --- | ------------ | ------ |
| | | | | | | relationaldata. | | InProc.ofNeurIPS. | | | |
| --- | --- | --- | --- | --- | --- | --------------- | --- | ----------------- | --- | --- | --- |
| | | | | | | grammars. | InProc.ofNAACL. | | | | |
| --- | --- | --- | --- | --- | --- | --------- | --------------- | --- | --- | --- | --- |
| kglm-model. | WealsointroducedLinkedWikiText- | | | | | | | | | | |
| ----------- | ------------------------------- | --- | --- | --- | --- | ------ | ------ | --------- | ----- | ----------- | ----- |
| | | | | | | Thiago | Castro | Ferreira, | Diego | Moussallem, | Emiel |
| | | | | | | WebNLGcorpus. | | InProc.ofINLG. | | | |
| ----------------- | ------ | ------------------------------- | --- | --------- | -------- | -------------- | ------------------ | ------------------- | --- | -------------- | ------ |
| the knowledge | | graph, allowing | | efficient | training | | | | | | |
| of the model. | Linked | WikiText-2 | | is freely | avail- | | | | | | |
| | | | | | | ClaireGardent, | | AnastasiaShimorina, | | ShashiNarayan, | |
| able for download | | at: https://rloganiv.github.io/ | | | | | | | | | |
| | | | | | | and Laura | Perez-Beltrachini. | | | 2017. The | WebNLG |
| that by utilizing | | this graph, | the | proposed | KGLM | | | | | | |
| ----------------- | --- | ----------- | --- | -------- | ---- | --- | --- | --- | --- | --- | --- |
| | | | | | | and Tie-Yan | | Liu. 2018. | Frage: | frequency-agnostic | |
| --- | --- | --- | --- | --- | --- | ----------- | --- | ---------- | ------ | ------------------ | --- |
| This work | lays | the groundwork | | for | future re- | | | | | | |
| --------- | ---- | -------------- | --- | --- | ---------- | ---------- | --------- | --- | ---- | ------- | ----------- |
| | | | | | | Jiatao Gu, | Zhengdong | Lu, | Hang | Li, and | Victor O.K. |
| | | | | | | Li. 2016. | | Incorporating | copying | mechanism | in |
| --------------- | --- | ----------- | --- | ------ | ----------- | --------- | --- | ------------- | ------- | ------------- | --- |
| The limitations | | of the KGLM | | model, | such as the | | | | | InProc.ofACL. | |
| lems for advancing | | neural | NLP | models. | Our dis- | | | | | | |
| ------------------ | --- | ------ | --- | ------- | -------- | ----------- | --- | --------------- | --- | --- | --- |
| | | | | | | andcontext. | | InProc.ofEMNLP. | | | |
| | | | | | | Choi, | and Noah | A. Smith. | 2017. | Dynamic | entity |
| --- | --- | --- | --- | --- | --- | -------------------------------------- | -------- | --------- | ----- | ------- | ------- |
| | | | | | | representationsinneurallanguagemodels. | | | | | InProc. |
| | | | | | | A method | for | stochastic | optimization. | | In Proc. of |
| ---------------------------------- | --- | --- | --- | --- | ------- | -------- | --- | ---------- | ------------- | --- | ----------- |
| inghisentitylinkertoassistourwork. | | | | | Wewould | | | | | | |
| fortheirthoughtfulfeedback. | | | Thisworkwassup- | | | | | | | | |
| --------------------------- | --- | --- | --------------- | --- | --- | ----------- | -------- | --------- | --- | ---- | ----------- |
| | | | | | | Ben Krause, | Emmanuel | Kahembwe, | | Iain | Murray, and |
| | | | | | | sequencemodels. | | InProc.ofICML. | | | |
| ---------- | ------ | --------------- | ------- | --------- | ----------- | --------------- | --- | -------------- | --- | --- | --- |
| telligence | (AI2), | and in | part by | NSF | award #IIS- | | | | | | |
| 1817183. | The | views expressed | | are those | of the | | | | | | |
| | | | | | | Stephen | Merity, | Nitish Shirish | | Keskar, | and Richard |
| --- | --- | --- | --- | --- | --- | --------------- | ------- | -------------- | --- | ---------- | ----------- |
| | | | | | | Socher. | 2018. | Regularizing | and | optimizing | LSTM |
| | | | | | | languagemodels. | | InProc.ofICLR. | | | |
| | | | | | | RichardSocher.2017. | | Pointersentinelmixturemod- | | | |
| --- | --- | --- | --- | --- | --- | ------------------- | --- | -------------------------- | --- | --- | --- |

Tomáš Mikolov, Martin Karafiát, Lukáš Burget, Jan
Cˇernocky`,andSanjeevKhudanpur.2010. Recurrent
neural network based language model. In Proc. of
INTERSPEECH.
Alec Radford, Jeff Wu, Rewon Child, David Luan,
DarioAmodei,andIlyaSutskever.2019. Language
modelsareunsupervisedmultitasklearners. Technicalreport,OpenAI.
Ehud Reiter and Robert Dale. 1997. Building applied
natural language generation systems. Natural LanguageEngineering,3(1):57–87.
IulianV.Serban, AlessandroSordoni, YoshuaBengio,
AaronCourville,andJoellePineau.2016. Building
end-to-enddialoguesystemsusinggenerativehierarchicalneuralnetworkmodels. InProc.ofAAAI.
Georgios P. Spithourakis and Sebastian Riedel. 2018.
Numeracyforlanguagemodels: Evaluatingandimprovingtheirabilitytopredictnumbers. InProc.of
ACL.
Nitish Srivastava, Geoffrey Hinton, Alex Krizhevsky,
Ilya Sutskever, and Ruslan Salakhutdinov. 2014.
Dropout: a simple way to prevent neural networks
fromoverfitting. TheJournalofMachineLearning
Research,15(1):1929–1958.
Trieu H. Trinh and Quoc V. Le. 2019. Do language
modelshavecommonsense? InProc.ofICLR.
Joerg Ueberla. 1994. Analysing a simple language
modelÂ·some general conclusions for language
modelsforspeechrecognition. ComputerSpeech&
Language,8(2):153–176.
Oriol Vinyals and Quoc V. Le. 2015. A neural conversational model. Proc. of ICML Deep Learning
Workshop.
Denny Vrandecˇic ́ and Markus Krötzsch. 2014. Wikidata: Afreecollaborativeknowledgebase. CommunicationsoftheACM,57(10):78–85.
Li Wan, Matthew Zeiler, Sixin Zhang, Yann LeCun,
andRobFergus.2013. Regularizationofneuralnetworksusingdropconnect. InProc.ofICML.
Sam Wiseman, Stuart M. Shieber, and Alexander M.
Rush.2017. Challengesindata-to-documentgeneration. InProc.ofEMNLP.
Zhilin Yang, Zihang Dai, Ruslan Salakhutdinov, and
WilliamWCohen.2018. Breakingthesoftmaxbottleneck:Ahigh-rankRNNlanguagemodel. InProc.
ofICLR.
Zichao Yang, Phil Blunsom, Chris Dyer, and Wang
Ling. 2017. Reference-aware language models. In
Proc.ofEMNLP.
5971