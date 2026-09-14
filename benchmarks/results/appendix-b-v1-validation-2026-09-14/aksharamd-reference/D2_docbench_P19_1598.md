Using Knowledge Graphs for Fact-Aware Language Modeling Robert L. Logan IV

§ Allen Institute for Artificial Intelligence, Seattle, WA, USA {rlogan, sameer}@uci.edu

Abstract

Modeling human language requires the ability only generate fluent code factual knowledge. models are only bering facts seen at training have difficulty recalling them. we introduce model (KGLM), a neural language model with mechanisms selecting graph context. These mechanisms model to render information it has never seen before, as well as generate tokens. We also introduce the a corpus of annotated text aligned to dataset, the Wikidata knowledge graph whose contents (roughly) match the popular Merity et al. mark ( demonstrate that cantly better performance line pare different language models’ ability to com- plete sentences requiring show that very large language models in generating facts.

Introduction

For models generate tences, they must be both syntactically coherent as well as consistent with the world they describe. though language models are quite skilled at generat- ing grammatical sentences, and previous work has shown that language models also possess some de- gree of common-sense reasoning and basic knowl- edge Vinyals Le 2015 Trinh and Le 2019 ), their ability to generate tually correct text quite limitation of existing language models is that they, at best, can only memorize facts observed during

https://rloganiv.github.io/linked-wikitext-2 *Proceedings of the 57th Annual Meeting of the Association for Computational Linguistics* Florence, Italy, July 28 - August 2, 2019. Barack’s Wife Hillary:

<sup>∗ †§ §</sup> Nelson F. Liu Matthew E. Peters <sup>§ ∗</sup> Matt Gardner Sameer Singh ∗ University of California, Irvine, CA, USA † University of Washington, Seattle, WA, USA

{mattg, matthewp}@allenai.org nfliu@cs.washington.edu

[ Super Mario Land ] is a [ ] [ side-scrolling ] [ platform video game ] developed and published [ Nintendo ] as a [ launch title ] for their [ Game text but also en- Boy ] [ handheld game console ] However, traditional <u>publisher</u> Nintendo Super Mario Land launch game Q8093 Q647249 Q1425505 capable remem- platform manufacturer time, often Publication genre Date To address this, 21 April 1989 platform game Game Boy graph Date Q828322 Q186437

instance of copying facts side-scrolling video game Q2281714 handheld game console that are relevant Q941818

enable Figure 1: Linked WikiText-2 Example A localized out-of-vocabulary graph containing facts that are (possibly) Linked WikiText- conveyed in the sentence above. The graph is built by it- eratively linking each detected entity to Wikidata, then adding any relations previously mentioned entities. WikiText-2 bench- Note that not all entities are connected, potentially due In experiments, we to missing relations in Wikidata. achieves signifi- than strong base- additionally com- training. For instance, when conditioned on the text at the top of Figure an AWD-LSTM language factual knowledge, model ( Merity et al. 2018 ) trained on Wikitext-2 outperforms even assigns higher probability word “ PlaySta- tion ” than “ Game Boy ”, even though this sentence appears verbatim in the training data. This is not surprising—existing models represent the distribu- plausible sen- tion over entire vocabulary directly, whether they are common words, references to real world Al- entities, or factual information like dates and num- bers. As a result, language models are unable to generate factually correct sentences, do gen- eralize to rare/unseen entities, and often omit rare tokens from the vocabulary (instead generating UN- ; Serban et al. ;

#### KNOWN

tokens). fac- introduce graph limited. clearest model (KGLM), model with mechanisms for selecting and copying information external graph. maintains a dynamically growing local knowledge

, pages 5962–5971 *⃝* 2019 Association for Computational Linguistics

graph , a subset of the knowledge graph that con- tains entities that have already been mentioned in the text, and their related entities. entity tokens, model either entity that absent thereby growing the local knowledge graph, or to render a fact from the local graph. ing, the model combines the standard vocabulary with tokens available in the knowledge graph, thus supporting numbers, dates, and other rare tokens. Figure illustrates how the KGLM works. tially, the graph is empty and the model uses the entity Super Mario Land tokens, thus adding it and its relations to the local knowledge graph. After generating the next two to- kens (“ ”, “ ”) using the standard language model, the model selects Super Mario entity, Publication Date as the relation to render, and copies one of the tokens of the date entity as the token (“ ” in this case). To facilitate research on knowledge graph-based modeling, we collect pervised dataset. ing text closely matches WikiText-2 ), a popular benchmark for language model- ing, allowing comparisons against els. The tokens in the text are linked to entities in Wikidata ( Vrandeˇci´c and Krötzsch combination of human-provided links and off-the- shelf linking and coreference models. relations between these entities in Wikidata to con- struct plausible reasons for why an entity may have been mentioned: could either entity that is already mentioned (including itself) or a brand new, unrelated entity for the document. train evaluate WikiText-2 When compared against AWD-LSTM, a recent and performant language model, KGLM obtains not only a lower overall perplexity, but also a substantially lower unknown-penalized ity ( Ueberla 1994 ; Ahn et al. allows fair comparisons between models that accu- rately model rare tokens and ones that predict them be unknown also compare pletion capabilities these predict the next word after a factual sentence (e.g., “ Barack is married to ”) and show that KGLM is significantly more accurate. the model is able to generate accurate facts for rare entities, can be controlled the knowledge graph. Knowledge Graph Language Model In this section we introduce a language model that When generating is conditioned on an external, structured knowledge decides render source, which it uses to generate factual text. local graph, 2.1 Problem Setup and Notation When render- A language model defines a probability distribution over each token within a sequence, conditioned on the sequence of tokens observed so far. We denote the random variable representing the next token as Ini- as and the sequence of the tokens before <t i.e. language models compute . RNN lan- <t render first three guage models ( Mikolov et al. 2010 ) parameterize this distribution using a recurrent structure:

) = softmax W h b <t + h (1) Land as the parent h RNN h − −

use LSTMs Hochreiter Schmidhuber 1997 ) as the recurrent module in this paper. A knowledge graph (KG) is a directed, labeled graph consisting of entities as nodes, with edges distantly su- defined over set relations R i.e. KG The underly- { p, r, e ∈E , r ∈R , e ∈E} , where is a par- Merity et al. ent entity with relation to another entity Prac- tical KGs have other aspects that make this for- mod- mulation somewhat inexact: some relations are to literal values such as numbers dates, facts 2014 ) using a may be expressed as properties on relations, and entities have aliases as the set of strings that can We also use refer entity. also define local knowl- edge graph for a subset of entities KG <t as <t { p, r, e ∈E , r ∈R , e ∈E} i.e. contains <t be <t and all facts they participate in.

2.2 Generative KG Language Model Linked primary goal graph lan- guage model (KGLM) enable lan- guage model generate facts graph. To encourage model generate facts that have appeared context perplex- already, KGLM will maintain a local knowledge ), a metric that graph containing facts involving that have appeared in the context. As the model decides factual com- refer that have been referred models, where they yet, will grow local graph with additional entities and facts to reflect the new entity. Formally, we will compute <t <t Lastly, we show that where <t is the sequence of observed tokens, <t is the set of entities mentioned in , and KG <t <t via modifications the local knowledge graph determined by , as <t described above. The generative process is:

**Super Mario Land is a 1989 side-scrolling platform video game developed and published by**

Relation to Existing Entity Mention of a New Entity

Not an Entity Mention

Figure 2:

KGLM Illustration. the type of the mention ( parent ( ), relation ( Nintendo The final distribution over the words includes the standard vocabulary along with aliases of and the model selects “

Decide type • : whether KG ), a reference to an entity not in <t KG ), or not an entity mention ( <t • If then choose the upcoming entity from the set of all entities • If then: – Choose a parent entity – Choose a factual relation ∈{ p, r, e – Choose as one of the tail entities, ∈{ • If ∅ then Generate • conditioned on ing one of ’s aliases. • If / ∈E , then <t else ←E < +1) For model refer mentioned, we introduce a self-relates, i.e. An illustration of this process and the variables is provided in Figure middle Amongst the three mention types ( chooses reference quires picking a fact to render. of this fact ( ), the model picks and then follows the parent from local entities

pl at f or m game si de- scr ol ng game

Super Mar i o Land pick from all entities

AAA I nc. Sony I nc.

Zzyzx, CA When trying to generate the token following “ ) to be a related entity (darker indicates higher probability), followed by identifying the ), and entity to render ( ) from the local knowledge graph as (

Nintendo ” as the token Facts related to which we denote reference entity

∅ <t to render, ∈KG } <t

, r , e ∈KG } <t ∅ , potentially copy- ←E } <t ∪{ < +1) <t

entity has already Reflexive relation that p, Reflexive , e , for generating a token in same sentence as Figure ), the model entity, which As the parent entity Super Mario Land Publisher relation ( ) to se-

#### Nintendo

Super Ni nt endo Mar i o Land standard vocabulary PUBLI SHER SELF

GENRE PLATFORM PUB. Game Boy DATE **company** **dog**

pl at f or m **...** game aliases of e

#### Kabushiki Koppai

Distribution over standard vocabulary and aliases of e

#### Nintendo

**...** Distribution over standard vocabulary

published by ”, the model first decides Super Mario Land Publisher Nintendo Nintendo will be added to the local graph.

Nintendo as entity render When lect rendering Nintendo as a token , the model has an expanded vocabulary available to it, containing the standard vocabulary along with all word types in any of the aliases of Marginalizing out the KG There is a mismatch between our initial task requirement, <t and the model we describe so far, which computes will essentially marginal- <t <t ize out the local knowledge graph to compute the probability of the tokens, i.e. ) = We will clarify this, along with describing the train- ing and the inference/decoding algorithms for this model and other details of the setup, in Section

2.3 Parameterizing the Distributions

The parametric distributions used in the generative process above are defined as follows. begin computing hidden state h using for- then split vector into mula Eqn three components: h [ h ; h ; h ] which t,x t,p t,r are respectively used to predict words, parents, and relations. type token, computed using single-layer softmax over h predict t,x re- one of { ∅} Picking an Entity We also introduce pretrained embeddings relations

knowledge graph, denoted by for relation To select from all entities in case , we use:

) = softmax h · t,p over all ∈E The reason we add to mimic the structure of TransE, which we use to obtain entity and relation embeddings. TransE will be provided in Section of a related entity, , we pick a parent entity using

) = softmax h · over all ∈E , then pick the relation

) = softmax h ·

over ∈{ , r, e ∈KG } determine tion must satisfy , r , e ∈KG ; if there are multi- ple options one is chosen at random). If Rendering Entity no entity render, we use same over the vocabulary as in Eqn - a softmax using h If there is an entity to render, t,x distribution over original a vocabulary containing all the tokens that appear aliases This distribution addition To compute over original vocabulary, h t,x <sup>′ ] where</sup> h <sup>W [ h ; W t,x proj</sup> t,x weight matrix that projects the concatenated vector into the same vector space as h t,x To obtain probabilities words vocabulary, we use copy mechanism The token sequences comprising each alias { } are embedded then encoded using an LSTM j to form vectors Copy scores are computed as: j

T <sup>′</sup> ∝ exp σ h j t,x

Modeling aside, one of the primary barriers to in- corporating factual knowledge into language mod- els is that training data is hard to obtain. modeling corpora consist and thus are unable to describe which entities or facts each token is referring to. In contrast, while relation extraction datasets link text to a knowledge for entity graph, the text is made up of disjoint sentences that do provide sufficient context train pow- erful language model. Our goals are much more Ahn et al. ; aligned to the data-to-text task ( h )) + t,r Lebret et al. ; Wiseman et al. ; Yang ; Gardent et al. ; Ferreira et al. et al. h h t,p t,r 2018 ), where a small table-sized KB is provided to generate a short piece of text; we are interested in Details on language models that dynamically decide the facts For mention to incorporate from the knowledge graph, guided by the discourse. For these reasons we introduce Linked WikiText-2 dataset, consisting of (approximately) t,p the same articles appearing in the WikiText-2 lan- using guage modeling corpus, but linked Wiki- data Vrandeˇci´c Krötzsch 2014 t,r graph. Because text closely matches, mod- els trained on can be compared combina- models trained WikiText-2 Furthermore, entity (which because many facts Wikidata are de- rived from Wikipedia articles, the knowledge graph has good coverage facts expressed ∅ i.e. there text. dataset available download at: distribution https://rloganiv.github.io/linked-wikitext-2 Our system annotates one document at a time, and con- we construct sists entity linking, relation annotations, vocabulary post-processing. following paragraphs de- scribe each step in detail. conditioned Initial entity annotations We begin by identify- scores ing an initial set of entity mentions within the text. replaced The primary source of these mentions is the human- <sup>is a learned proj</sup> provided links between Wikipedia articles. When- ever a span of text is linked to another Wikipedia article, we associate its corresponding Wikidata alias entity with the span. While article links provide a Gu et al. large number of gold entity annotations, they are in- sufficient for capturing all of the mentions in the ar- ticle since entities are only linked the first time they occur. Accordingly, we use the neural-el Gupta et al. ) entity linker to identify additional links W copy j to Wikidata, and identify coreferences using Stan- ford CoreNLP to cover pronouns, nominals, and other tokens missed by the linker.

Local knowledge graph The next step iteratively creates a generative story for the entities using rela- tions in the knowledge graph as well as identifies Standard new entities. To do this, we process the text token only text, by token. Each time an entity is encountered, we add all of the related entities in Wikidata as candi-

https://stanfordnlp.github.io/CoreNLP/

Tokens Super Mention type Entity Mentioned Relation Parent Entity

published <u>Nintendo</u> ∅ ∅ ∅ NIN ∅ ∅ ∅ ∅ ∅ ∅ pub SML ∅ ∅ ∅

Table 1: Example Annotation Note that Game Boy has multiple manufactured by Nintendo Wikidata identifiers are made human-readable (e.g.,

| | | Train | Dev | Test |
| --- | --- | --- | --- | --- |
| Documents Tokens Vocab. Mention Tokens Mention Spans Unique Entities Unique Relations | Size | 600 2,019,195 33,558 207,803 122,983 41,058 1,291 | 60 207,982 - 21,226 12,214 5,415 484 | 60 236,062 - 24,441 15,007 5,625 504 |

dates for matching. If one of these related entities is seen later in the document, we identify the entity as a parent for the later entity. lations may appear as explanations we allow a token to have multiple facts. Expanding the annotations entities that were missed in the initial set, as well as non-entity tokens of interest such as dates and quantities we further expand the entity annotations using string matching. For entities, we match the set of aliases provided in Wikidata. create an exhaustive list of all of the possible ways of expressing the date (e.g. " 7-12-1941 ", " 1941 ", ...). approach quantities, using Python to handle the different ways of expressing units (e.g. " g ", " gram ", ...). ways to express a numerical quantity, we only ren- der the quantity at the level of precision supplied by Wikidata, and do not perform unit conversions. Example Annotation An example annotation is provided in Table corresponding to the instance in Figure , along with the variables that correspond to the generative process of the knowledge graph language model (KGLM). The entity mentioned for most tokens here are human-provided links, apart “ ” that linked string matching process. The annotations indicate which of the entities are whether they are reachable by entities linked so far, clearly making a mistake for platform video game due to missing links in Wikidata. Finally, multiple Game Boy are included: it’s the platform for Mario Land manufactured even though only the former is more relevant here. Mario Land side - <u>scrolling</u> platform video game developed ∅∅ ∅ SML ∅∅ 04-21-1989 SIDE_SCROLL PVG ∅ ∅ ∅∅ pub date ∅ genre ∅ ∅ ∅∅ SML ∅ SML ∅

as <u>launch</u> title their Game Boy handheld game console ∅ ∅ ∅ ∅ ∅ ∅ ∅ LT ∅ ∅ GAME_BOY HGC ∅ ∅ ∅ ∅ ∅ ∅ R:manu / platform instance ∅ ∅ ∅ ∅ ∅ ∅ NIN / SML GAME_BOY ∅

sentence Figure including corresponding variables Figure parent relation annotations, as platform Super Mario Land as SML is Q647249) for clarity.

Since multiple re- for each token, Since there may be

Table 2:

Corpus Statistics.

Even with these omissions and mistakes, it is clear For dates, we that annotations are rich detailed, with high coverage, and thus should prove beneficial for " December 7, 1941 ", training knowledge graph language models. perform similar Dataset Statistics Statistics for pint library are provided in Table In this corpus, more than 10% of the tokens are considered entity tokens, i.e. Since there are many they are generated as factual references to informa- tion in the knowledge graph. Each entity is only mentioned a few times (less than 5 on average, with a long tail), and with more than thousand different relations. Thus clear that regular models would not be able to generate factual text, and there is a need for language models to be able to refer to external sources of information. Differences WikiText-2 Although our 04-21-1989 dataset is designed to closely replicate WikiText-2 there are some differences that prevent direct com- based on parison. Firstly, there are minor variations in text across articles due to edits between download dates. side-scrolling game Secondly, according to correspondence with Merity et al. ), WikiText-2 was collected by querying the Wikipedia Text API. Because this API discards plausible reasons Super useful annotation information (e.g. article links), Nintendo instead was created by directly from the article HTML.

Training and Inference for KGLM In this section, we describe the training and infer- ence algorithm for KGLM. Pretrained KG Embeddings we may need to make predictions on entities and relations that have not been seen during training. Accordingly, we use fixed entity and relations em- beddings pre-trained using TransE ( 2013 ) on Wikidata. Given beddings

δ ) = We use a max-margin loss to learn the embeddings:

max 0 , γ + δ where γ is the margin, and either domly chosen entity embedding. Training with Linked generative process in KGLM involves many steps, training the model on forward. Our loss objective likelihood of the training data:

log ℓ (Θ) =

where Θ is the set of model parameters. if an annotation has multiple viable parents such as Game Boy , then we marginalize over all of the parents. Since all random variables are observed, training can performed using off-the-shelf gradient- based optimizers. While observing annotations makes the Inference model easy train, model has access to annotations during evaluation. Furthermore, as discussed in Section in language modelling is to measure the marginal probability ) = bility. However, this sum is intractable to compute due large combinatorial annotations. We address this problem by approxi- mating the marginal distribution using importance sampling. Given samples from a proposal distribu- tion q the marginal distribution is:

) = ≈ N q ∼ q During evaluation,

Bordes et al. p, r, e we learn em- to minimize the distance:

∥ ∥ + − <sup>′</sup> − δ

<sup>′ ′</sup> or WikiText-2 Although

is straight- negative ; Θ) <t <t

Note that we do assume that

2.2 , the goal <sup>not the joint proba-</sup>

space possible ) = <sup>q</sup> q This approach is used to evaluate models in Ji et al. ) and Dyer et al. Following Ji et al. ), we compute q using a discriminative version of our model that predicts annotations for the current token instead of for the next token.

5 Experiments To evaluate proposed model, we first introduce the baselines, followed by an evalua- tion using perplexity of held-out corpus, accuracy on fact completion, and an illustration of how the model uses the knowledge graph.

5.1 Evaluation Setup <sup>′</sup> We compare KGLM to the fol- Baseline Models lowing baseline models: is a ran- •

#### AWD-LSTM

Merity et al. 2018 ): strong LSTM-based model used as foundation most state-of-the-art models on WikiText-2 Ji et al. ): an LSTM-based • NTITY NLM model with ability track entity log- mentions. Embeddings for entities are created dy- namically, and are not informed by any external sources of information. • EntityCopyNet: a variant of the KGLM where mentions, i.e. are selected from and entity aliases are copied, but relations in the knowledge graph are unused.

We pre-train 256 dimensional Hyperparameters entity relation embeddings within two hops of the set of entities that occur in using TransE with margin γ = 1 Weights are tied between all date embeddings and between all quantity embeddings to save memory. Following Merity et al. 2018 ) we use 400 dimen- sional word embeddings and a 3 layer LSTM with hidden dimension 1150 to encode tokens. We also employ the same regularization strategy (DropCon- nect ( Wan et al. 2013 ) + Dropout( Srivastava et al. 2014 )) and weight tying approach. However, we perform optimization using Adam ( Kingma and Ba 2015 ) with learning rate 1e-3 instead of NT-ASGD, having found that it is more stable.

5.2 Results We evaluate our model using the stan- Perplexity T <sup>log</sup> dard perplexity metric: exp =1 T However, perplexity suffers from the issue that it

| | | | | | | | PPL | UPP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| E NTITY EntityCopyNet AWD-LSTM ( KGLM | NLM * | * ( Ji et al. * Merity et al. | , 2017 | , 2018 | ) | ) | 85.4 76.1 74.8 44.1 | 189.2 144.0 165.8 88.5 |

Table 3:

Perplexity Results sults for models marked with portance sampling.

overestimates the probability of out-of-vocabulary tokens when they are mapped token. This is problematic for comparing the per- formance models on number of rare entities whose alias tokens are out- of-vocabulary. That is, even if the KGLM identifies the correct entity and copies the correct alias token with high probability, other models can attain bet- ter perplexity by assigning a higher probability to UNK. Accordingly, we also measure nalized perplexity (UPP) (a.k.a introduced by Ueberla 1994 Ahn et al. ) and Spithourakis and Riedel 2018 This metric penalizes UNK tokens by evenly dividing their probability mass over U set tokens UNK can be compute UNK perplexity where |U| is estimated from the data. We present the model perplexities in Table marginalize over annotations, perplexities for the NTITY NLM, EntityCopyNet, and KGLM are es- timated using the importance sampling approach described in Section We observe that the KGLM attains substantially lower perplexity than the other entity-based language models (44.1 vs. providing strong evidence that leveraging knowl- edge graphs is crucial for accurate language mod- eling. Furthermore, forms all models in unknown penalized perplexity, demonstrating its ability to generate rare tokens.

Fact Completion Since factual our primary objective, we models complete factual information. We additionally compare with small GPT-2 ( Radford et al. model trained on a much larger corpus of text. select popular relations from Freebase, and write a simple completion template for each, such as “ was born ” birthplace AWD- GPT-2 LSTM Oracle NEL nation-capital 0 / 0 / 0 / 0 0 / 4 birthloc 0 / 9 14 / 14 94 / 95 85 / 92 birthdate 0 / 25 8 / 9 65 / 68 61 / 67 spouse 0 / 0 / 3 / 2 1 / 19 city-state 0 / 13 62 / 62 9 / 59 4 / 59 Re- book-author 0 / 2 0 / 0 61 / 62 25 / 28 * are obtained using im- Average 0.0/8.2 15.3/15.8 38.5 / 47.7 29.3/44.8

Table 4: Fact Completion Top- k accuracy (@1/@5,%) for predicting the next token for an incom- plete factual sentence. See examples in Table 5 single UNK

generate sentences for these templates for a number traditional

#### X, Y

pairs for which the relation holds, and since there are a large manually examine the first token generated by each language model to determine whether it is correct. Table presents performance of each language model on the relations. oracle KGLM is given the correct entity annotation for , while the NEL KGLM uses the discriminative model used for im- unknown pe- portance sampling combined with the NEL entity adjusted perplexity linker to produce an entity annotation for ), and used recently Amongst models trained on the same data, both variants significantly outperform AWD- probability LSTM; they produce accurate facts, while AWD- LSTM produced generic, common words. KGLMs that get mapped are also competitive with models trained on orders UPP replacing magnitude more data, producing factual com- <sup>UNK</sup> above |U| pletions that require specific knowledge, such as birthplaces, dates, and authors. However, they do To not capture facts or relations that frequently appear in large corpora, like the cities within states. It is encouraging to see that the KGLM with automatic linking performs comparably to oracle linking. provide examples Table 5 highlight qualitative differences between KGLM, trained on 76.1/85.4), 600 documents, and the recent state-of-the-art lan- guage model, GPT-2, trained on the WebText cor- pus with over 8 million documents ( Radford et al. significantly outper- 2019 For examples that both models get factu- ally correct or incorrect, the generated tokens by KGLM are often much more specific, as opposed to selection of more popular/generic tokens (GPT-2 text generation often predicts “New York” as the birthplace, even evaluate ability popular entities). KGLM, particular, gets sentences with factual statements correct when the head or tail en- tities are rare, while GPT-2 can only complete facts 2019 ), a language for more-popular entities while using more-generic <u>tokens (such as “</u> January ” instead of “ 20 ”). This is not a failure of the KG, but of the model’s ability relation. to pick the correct relation from the KG given the prompt.

Input Sentence Paris Hilton was born in Both correct Arnold Schwarzenegger was born on Bob Dylan was born in Barack Obama was born on KGLM correct Ulysses is a book that was written by St. Louis is a city in the state of GPTv2 correct Richard Nixon was born on Kanye West is married to The capital of India is Both incorrect Madonna is married to

Table 5:

Completion Examples Examples of fact completion by KGLM and GPT-2, which has been trained on much larger corpus. GPT-2 tends produce cities to follow “ born in ”. KGLM sometimes makes mistakes in linking to the appropriate fact in the KG, however, the generated facts are more specific and contain produced tokens apart from the generic “

For most Effect changing KG models, difficult control their generation since factual knowledge is entangled with gener- ation capabilities For KGLM, additional benefit of its use of an external source that directly lable via modifications to the KG. To illustrate this capability with a simple example, we create com- pletion of “ Barack Obama was born on the original fact ( Barack Obama birthDate ), resulting in the top three decoded tokens as “ August ”, “ ”, “ 1961 ”. After changing the birth date to 2013-03-21 , the top three decoded tokens become “ March ”, “ 21 ”, “ 2013 ”. Thus, changing the fact in the knowledge graph directly leads to a corresponding change in the model’s prediction.

Related Work

Knowledge-based models draws inspiration two knowledge- based language models: (i) NTITY NLM Ji et al. proves a language model’s ability to track entities by jointly modeling named entity recognition and coreference. Our model similarly tracks through a document, improving its ability to gener- ate factual information by modeling entity linking and relation extraction. (ii) (NKLM) ( Ahn et al. ) which established the idea of leveraging knowledge graphs in neural lan- guage models. The main differentiating factor be- tween the KGLM and NKLM is that the KGLM operates on an entire knowledge graph and can be Gold GPT-2 New York City New 1947-07-30 July Duluth New Duluth 1961-08-04 January August James Joyce James Missouri Missouri Oldham 1913-01-09 January 20 Kim Kardashian Kim New Delhi Carlos Leon Alex

very common general tokens, such as one few popular rare tokens. omit AWD-LSTM from this figure as it rarely ” or “ ”, or “ ⟨ UNK ⟩ ”.

evaluated on text without additional conditioning information, whereas the NKLM operates on a rel- atively smaller set of predefined edges emanating from a single entity, and requires that entity be pro- vided as conditioning information ahead time. This requirement precludes direct comparison be- control- tween NKLM and the baselines in Section 5

Our work is also related Data-to-text generation ” with task of data-to-text generation. For 1961- a survey of early non-neural text generation meth- ods we refer the reader to Reiter and Dale 1997 Recent neural methods have been applied to gener- Wiseman ating text from tables of sports statistics ( et al. ), lists and tables ( Yang et al. ), and Wikipedia info-boxes ( Lebret et al. The pri- mary difference between these works and ours is our motivation. These works focus on generating coherent text within a narrow domain (e.g. sports, recipes, introductory sentences), and optimize met- Our work rics such as BLEU and METEOR score. Our focus instead is to use a large source of structured knowl- edge to improve language model’s ability to handle which im- rare tokens and facts on a broad domain of topics, and our emphasis is on improving perplexity.

General language modeling Also related are the recent papers proposing modifications to the AWD-

#### LSTM

that improve performance Wikitext- Gong et al. 2018 ; Yang et al. 2018 ; Krause model et al. 2018 ). We chose to benchmark against AWD-

#### LSTM

since these contributions are orthogonal, many techniques are compatible with the KGLM. KGLM improves upon AWD-LSTM, and we expect using KGLM in conjunction with these methods will yield further improvement.

Conclusions and Future Work By relying memorization, models are unable to generate factually correct text about real-world entities. unable to capture the long tail of rare entities and word types like numbers and dates. we proposed the knowledge graph language model (KGLM), a neural language model that can access an external source of facts, encoded as a knowledge graph, in order to generate text. tion is available at: https://github.com/rloganiv/ We also introduced kglm-model containing text that has been aligned to facts in graph, allowing Linked WikiText-2 able for download at: https://rloganiv.github.io/ In our evaluation, linked-wikitext-2 that by utilizing this graph, the proposed KGLM is able to generate higher-quality, factually correct text that includes mentions of rare entities and spe- cific tokens like numbers and dates. This work lays groundwork search into knowledge-aware language modeling. The limitations of the KGLM model, such as the need for marginalization during inference and re- liance on annotated tokens, raise new research prob- lems for advancing neural NLP models. tantly supervised approach to dataset creation can be used with other kinds of text as well, providing opportunities for accurate language modeling in new domains.

Acknowledgements First and foremost, we would like to thank Stephen Merity for sharing the materials used to collect the WikiText-2 dataset, Nitish ing his entity linker to assist our work. also like to thank Dheeru Dua and Anthony Chen for their thoughtful feedback. ported part Allen Institute telligence (AI2), and in part by NSF award #IIS- 1817183. views expressed authors and do not reflect the official policy or po- sition of the funding agencies. References Sungjin Ahn, Heeyoul Choi, Tanel Pärnamaa, Yoshua Bengio. 2016. A neural knowledge language ArXiv:1608.00318. In particular, they are Antoine Bordes, Nicolas Usunier, Alberto Garcia- Duran, Jason Weston, Oksana Yakhnenko. In this work, 2013. Translating embeddings modeling multi- relational data. In Proc. of NeurIPS

Chris Dyer, Adhiguna Kuncoro, Miguel Ballesteros, and Noah A. Smith. 2016. Recurrent neural network Our implementa- grammars. In Proc. of NAACL

Linked WikiText- Thiago Castro Ferreira, Diego Moussallem, Emiel Krahmer, and Sander Wubben. 2018. Enriching the WebNLG corpus. In Proc. of INLG efficient training freely avail- Claire Gardent, Anastasia Shimorina, Shashi Narayan, Laura Perez-Beltrachini. 2017. WebNLG we showed challenge: Generating text from RDF data. In Proc. of INLG

Chengyue Gong, Di He, Xu Tan, Tao Qin, Liwei Wang, Tie-Yan Liu. 2018. Frage: frequency-agnostic word representation. In Proc. of NeurIPS future re- Jiatao Gu, Zhengdong Lu, Hang Li, Victor O.K. Li. 2016. Incorporating copying mechanism sequence-to-sequence learning. In Proc. of ACL

Nitish Gupta, Sameer Singh, and Dan Roth. 2017. En- tity linking via joint encoding of types, descriptions, Our dis- and context In Proc. of EMNLP

Sepp Hochreiter Jürgen Schmidhuber. 1997. graphs other Long short-term memory. Neural computation 9(8):1735–1780.

Yangfeng Ji, Chenhao Tan, Sebastian Martschat, Yejin Choi, Noah A. Smith. 2017. Dynamic entity representations in neural language models. In Proc. of EMNLP

Diederik P. Kingma Jimmy Ba. 2015. Adam: Gupta modify- A method stochastic optimization. In Proc. We would ICLR

This work was sup- Ben Krause, Emmanuel Kahembwe, Iain Murray, Steve Renals. 2018. Dynamic evaluation Artificial In- sequence models. In Proc. of ICML

are those Rémi Lebret, David Grangier, and Michael Auli. 2016. Neural text generation structured data with application biography domain. In Proc. EMNLP

Stephen Merity, Nitish Shirish Keskar, Richard Socher. 2018. Regularizing optimizing LSTM language models. In Proc. of ICLR

Stephen Merity, Caiming Xiong, James Bradbury, and Richard Socher. 2017. Pointer sentinel mixture mod- els. In Proc. of ICLR

Tomáš Mikolov, Martin Karafiát, Lukáš Burget, Jan ˇCernock`y, and Sanjeev Khudanpur. 2010. Recurrent network based In Proc. INTERSPEECH

Alec Radford, Jeff Wu, Rewon Child, David Luan, Dario Amodei, and Ilya Sutskever. 2019. Language models are unsupervised multitask learners. Techni- cal report, OpenAI.

Ehud Reiter Robert Dale. 1997. Building applied natural generation systems. Natural Lan- guage Engineering , 3(1):57–87.

Iulian V. Serban, Alessandro Sordoni, Yoshua Bengio, Aaron Courville, and Joelle Pineau. 2016. Building end-to-end dialogue systems using generative hierar- chical neural network models. In Proc. of AAAI

Georgios P. Spithourakis Sebastian Riedel. 2018. Numeracy for language models: Evaluating and im- proving their ability to predict numbers. In Proc. of ACL

Nitish Srivastava, Geoffrey Hinton, Alex Krizhevsky, Ilya Sutskever, Ruslan Salakhutdinov. 2014. Dropout: simple way prevent networks from overfitting. The Journal of Machine Learning Research , 15(1):1929–1958.

Trieu H. Trinh Quoc V. Le. 2019. Do models have common sense? In Proc. of ICLR.

Joerg Ueberla. 1994. Analysing simple modelÂ · some general conclusions Computer Speech & models for speech recognition Language , 8(2):153 – 176.

Oriol Vinyals Quoc V. Le. 2015. A con- versational Proc. ICML Deep Learning Workshop

Denny Vrandeˇci´c Markus Krötzsch. 2014. Wiki- data: A free collaborative knowledgebase Commu- nications of the ACM , 57(10):78–85.

Wan, Matthew Zeiler, Sixin Zhang, Yann LeCun, and Rob Fergus. 2013. Regularization of neural net- works using dropconnect. In Proc. of ICML

Sam Wiseman, Stuart M. Shieber, Alexander M. Rush. 2017. Challenges in data-to-document gener- ation. In Proc. of EMNLP

Zhilin Yang, Zihang Dai, Ruslan Salakhutdinov, William W Cohen. 2018. Breaking the softmax bot- tleneck: A high-rank RNN language model. In Proc. of ICLR

Zichao Yang, Phil Blunsom, Chris Dyer, Wang Ling. 2017. Reference-aware models. In Proc. of EMNLP