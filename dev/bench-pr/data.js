window.BENCHMARK_DATA = {
  "lastUpdate": 1786123153344,
  "repoUrl": "https://github.com/priyanshu-krishnan1/yaadien",
  "entries": {
    "Benchmark": [
      {
        "commit": {
          "author": {
            "name": "priyanshu-krishnan1",
            "username": "priyanshu-krishnan1"
          },
          "committer": {
            "name": "priyanshu-krishnan1",
            "username": "priyanshu-krishnan1"
          },
          "id": "6b38d44b3674f62c729367454c29780f0d96ef20",
          "message": "build(deps): bump h2 from 4.4.0 to 4.4.1 in the uv group across 1 directory",
          "timestamp": "2026-08-07T17:00:25Z",
          "url": "https://github.com/priyanshu-krishnan1/yaadien/pull/25/commits/6b38d44b3674f62c729367454c29780f0d96ef20"
        },
        "date": 1786123097262,
        "tool": "pytest",
        "benches": [
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_adapter[noop]",
            "value": 1149.8909909842534,
            "unit": "iter/sec",
            "range": "stddev: 0.00010262525671645902",
            "extra": "mean: 869.6476516822228 usec\nrounds: 178"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_adapter[resolver_on]",
            "value": 1161.6295785654213,
            "unit": "iter/sec",
            "range": "stddev: 0.00008850091343110325",
            "extra": "mean: 860.85962207933 usec\nrounds: 942"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_adapter[consolidator_on]",
            "value": 1183.9531833726894,
            "unit": "iter/sec",
            "range": "stddev: 0.00010208803305610511",
            "extra": "mean: 844.6279920894609 usec\nrounds: 885"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_adapter[fully_wired]",
            "value": 1174.088368529961,
            "unit": "iter/sec",
            "range": "stddev: 0.00009206265322355928",
            "extra": "mean: 851.7246459498348 usec\nrounds: 1062"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_direct[noop]",
            "value": 1572.4295423123283,
            "unit": "iter/sec",
            "range": "stddev: 0.00005784961848352827",
            "extra": "mean: 635.9585425553981 usec\nrounds: 282"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_direct[resolver_on]",
            "value": 1319.226557638694,
            "unit": "iter/sec",
            "range": "stddev: 0.00008848573655825508",
            "extra": "mean: 758.0199126599733 usec\nrounds: 1248"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_direct[consolidator_on]",
            "value": 1522.5099678443607,
            "unit": "iter/sec",
            "range": "stddev: 0.0000962939955912797",
            "extra": "mean: 656.8101497659459 usec\nrounds: 1075"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_before_run_direct[fully_wired]",
            "value": 1649.7775883230527,
            "unit": "iter/sec",
            "range": "stddev: 0.0000885427735987711",
            "extra": "mean: 606.1423109865789 usec\nrounds: 1074"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_adapter[noop]",
            "value": 324.11208213918354,
            "unit": "iter/sec",
            "range": "stddev: 0.0007632195023020427",
            "extra": "mean: 3.085352429319712 msec\nrounds: 191"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_adapter[resolver_on]",
            "value": 336.5939680273298,
            "unit": "iter/sec",
            "range": "stddev: 0.0005136513324375612",
            "extra": "mean: 2.970938563934113 msec\nrounds: 305"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_adapter[consolidator_on]",
            "value": 342.0592550300291,
            "unit": "iter/sec",
            "range": "stddev: 0.00020074378597277997",
            "extra": "mean: 2.923470086819346 msec\nrounds: 311"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_adapter[fully_wired]",
            "value": 348.9470582110892,
            "unit": "iter/sec",
            "range": "stddev: 0.00019865874551312787",
            "extra": "mean: 2.865764236920628 msec\nrounds: 325"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_direct[noop]",
            "value": 368.5584291489002,
            "unit": "iter/sec",
            "range": "stddev: 0.0007393171497239237",
            "extra": "mean: 2.7132739910718278 msec\nrounds: 336"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_direct[resolver_on]",
            "value": 382.91633976168106,
            "unit": "iter/sec",
            "range": "stddev: 0.0001400318897587445",
            "extra": "mean: 2.6115365059176594 msec\nrounds: 338"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_direct[consolidator_on]",
            "value": 375.21694944614643,
            "unit": "iter/sec",
            "range": "stddev: 0.00016218686249448868",
            "extra": "mean: 2.6651248070645233 msec\nrounds: 368"
          },
          {
            "name": "benchmarks/adapters/test_agent_framework_adapter.py::test_agent_framework_after_run_direct[fully_wired]",
            "value": 382.6070128854323,
            "unit": "iter/sec",
            "range": "stddev: 0.00021299659207672802",
            "extra": "mean: 2.6136478588264653 msec\nrounds: 340"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_adapter[noop]",
            "value": 391.5652916342402,
            "unit": "iter/sec",
            "range": "stddev: 0.0002988021165418854",
            "extra": "mean: 2.553852502673032 msec\nrounds: 374"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_adapter[resolver_on]",
            "value": 370.5777612543167,
            "unit": "iter/sec",
            "range": "stddev: 0.0003600098775350199",
            "extra": "mean: 2.698488966567341 msec\nrounds: 329"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_adapter[consolidator_on]",
            "value": 375.6615486581212,
            "unit": "iter/sec",
            "range": "stddev: 0.00019219537326528404",
            "extra": "mean: 2.6619706051152745 msec\nrounds: 352"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_adapter[fully_wired]",
            "value": 368.27042249751753,
            "unit": "iter/sec",
            "range": "stddev: 0.000202565017942831",
            "extra": "mean: 2.7153959126509566 msec\nrounds: 332"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_direct[noop]",
            "value": 385.1284508181047,
            "unit": "iter/sec",
            "range": "stddev: 0.0002448664115951342",
            "extra": "mean: 2.5965362929582625 msec\nrounds: 355"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_direct[resolver_on]",
            "value": 372.3308366224541,
            "unit": "iter/sec",
            "range": "stddev: 0.0002569485943010975",
            "extra": "mean: 2.6857834528865694 msec\nrounds: 329"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_direct[consolidator_on]",
            "value": 373.6330606535839,
            "unit": "iter/sec",
            "range": "stddev: 0.00020214753553398303",
            "extra": "mean: 2.6764226866079066 msec\nrounds: 351"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_add_message_direct[fully_wired]",
            "value": 372.00299618101616,
            "unit": "iter/sec",
            "range": "stddev: 0.00016845490450950233",
            "extra": "mean: 2.6881503919753413 msec\nrounds: 324"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_adapter[noop]",
            "value": 490.36592594455055,
            "unit": "iter/sec",
            "range": "stddev: 0.0001314196834841484",
            "extra": "mean: 2.0392934074156646 msec\nrounds: 54"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_adapter[resolver_on]",
            "value": 497.3855101986457,
            "unit": "iter/sec",
            "range": "stddev: 0.00010870784493227043",
            "extra": "mean: 2.010512931107744 msec\nrounds: 479"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_adapter[consolidator_on]",
            "value": 495.6934025678244,
            "unit": "iter/sec",
            "range": "stddev: 0.00012839312559976342",
            "extra": "mean: 2.0173760530596785 msec\nrounds: 490"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_adapter[fully_wired]",
            "value": 494.6093540809672,
            "unit": "iter/sec",
            "range": "stddev: 0.00011922492331569958",
            "extra": "mean: 2.0217975898537106 msec\nrounds: 473"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_direct[noop]",
            "value": 499.3473282772646,
            "unit": "iter/sec",
            "range": "stddev: 0.00009092020567200462",
            "extra": "mean: 2.0026140991881825 msec\nrounds: 494"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_direct[resolver_on]",
            "value": 504.4254340775661,
            "unit": "iter/sec",
            "range": "stddev: 0.00008809500907793478",
            "extra": "mean: 1.9824535648736317 msec\nrounds: 501"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_direct[consolidator_on]",
            "value": 508.7522883877385,
            "unit": "iter/sec",
            "range": "stddev: 0.0001750040021743238",
            "extra": "mean: 1.9655931242472642 msec\nrounds: 499"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_list_messages_direct[fully_wired]",
            "value": 505.3841184013258,
            "unit": "iter/sec",
            "range": "stddev: 0.00010895038131650718",
            "extra": "mean: 1.9786929655868204 msec\nrounds: 494"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_adapter[noop]",
            "value": 420.041730254082,
            "unit": "iter/sec",
            "range": "stddev: 0.00012230824914499485",
            "extra": "mean: 2.380715838388493 msec\nrounds: 99"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_adapter[resolver_on]",
            "value": 422.55012201926917,
            "unit": "iter/sec",
            "range": "stddev: 0.00012331230261681687",
            "extra": "mean: 2.36658315283695 msec\nrounds: 229"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_adapter[consolidator_on]",
            "value": 414.5162136216848,
            "unit": "iter/sec",
            "range": "stddev: 0.00028944456701034653",
            "extra": "mean: 2.4124508695639753 msec\nrounds: 230"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_adapter[fully_wired]",
            "value": 417.51590965041174,
            "unit": "iter/sec",
            "range": "stddev: 0.00013821202958820452",
            "extra": "mean: 2.3951183101915934 msec\nrounds: 216"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_direct[noop]",
            "value": 860.558121061055,
            "unit": "iter/sec",
            "range": "stddev: 0.00008337260521529643",
            "extra": "mean: 1.1620365615363846 msec\nrounds: 260"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_direct[resolver_on]",
            "value": 873.3895903405576,
            "unit": "iter/sec",
            "range": "stddev: 0.00007122016234243658",
            "extra": "mean: 1.1449644134298345 msec\nrounds: 283"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_direct[consolidator_on]",
            "value": 855.614152355914,
            "unit": "iter/sec",
            "range": "stddev: 0.00007055812517403127",
            "extra": "mean: 1.1687511213395931 msec\nrounds: 239"
          },
          {
            "name": "benchmarks/adapters/test_langchain_adapter.py::test_langchain_mset_direct[fully_wired]",
            "value": 865.0477157539368,
            "unit": "iter/sec",
            "range": "stddev: 0.00005273535675127244",
            "extra": "mean: 1.1560055957473336 msec\nrounds: 282"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_tool[noop]",
            "value": 342.6443947461406,
            "unit": "iter/sec",
            "range": "stddev: 0.00011913664766776122",
            "extra": "mean: 2.9184776267561094 msec\nrounds: 284"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_tool[resolver_on]",
            "value": 339.90946814993146,
            "unit": "iter/sec",
            "range": "stddev: 0.0001497966192244306",
            "extra": "mean: 2.9419598266645153 msec\nrounds: 300"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_tool[consolidator_on]",
            "value": 344.1815474575098,
            "unit": "iter/sec",
            "range": "stddev: 0.00012364963601354132",
            "extra": "mean: 2.905443384129862 msec\nrounds: 315"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_tool[fully_wired]",
            "value": 344.8443561422545,
            "unit": "iter/sec",
            "range": "stddev: 0.00016010906125867372",
            "extra": "mean: 2.8998589717022423 msec\nrounds: 318"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_direct[noop]",
            "value": 383.4741436456912,
            "unit": "iter/sec",
            "range": "stddev: 0.00012163019126596585",
            "extra": "mean: 2.6077377486080118 msec\nrounds: 358"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_direct[resolver_on]",
            "value": 375.7059514827523,
            "unit": "iter/sec",
            "range": "stddev: 0.00015043637083404386",
            "extra": "mean: 2.661656000000595 msec\nrounds: 365"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_direct[consolidator_on]",
            "value": 378.0850976797798,
            "unit": "iter/sec",
            "range": "stddev: 0.00015635358097947636",
            "extra": "mean: 2.6449072077602827 msec\nrounds: 361"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_remember_direct[fully_wired]",
            "value": 379.5367267800855,
            "unit": "iter/sec",
            "range": "stddev: 0.00014740625282644418",
            "extra": "mean: 2.634791126760781 msec\nrounds: 355"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_tool[noop]",
            "value": 445.8959762213321,
            "unit": "iter/sec",
            "range": "stddev: 0.00008641816593657818",
            "extra": "mean: 2.242675541668543 msec\nrounds: 360"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_tool[resolver_on]",
            "value": 446.13438469855123,
            "unit": "iter/sec",
            "range": "stddev: 0.00017477033390962267",
            "extra": "mean: 2.2414770847032797 msec\nrounds: 425"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_tool[consolidator_on]",
            "value": 449.61721563479006,
            "unit": "iter/sec",
            "range": "stddev: 0.00009969404891837579",
            "extra": "mean: 2.2241141246963916 msec\nrounds: 409"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_tool[fully_wired]",
            "value": 455.49911323166253,
            "unit": "iter/sec",
            "range": "stddev: 0.00009505380050256227",
            "extra": "mean: 2.1953939556659674 msec\nrounds: 406"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_direct[noop]",
            "value": 496.4601603494124,
            "unit": "iter/sec",
            "range": "stddev: 0.00015922839626772835",
            "extra": "mean: 2.0142603170739672 msec\nrounds: 492"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_direct[resolver_on]",
            "value": 505.2845149346863,
            "unit": "iter/sec",
            "range": "stddev: 0.00020290407310088858",
            "extra": "mean: 1.979083012526638 msec\nrounds: 479"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_direct[consolidator_on]",
            "value": 514.2010469709894,
            "unit": "iter/sec",
            "range": "stddev: 0.00013915032808782104",
            "extra": "mean: 1.9447646127729854 msec\nrounds: 501"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_list_memories_direct[fully_wired]",
            "value": 504.14387022884193,
            "unit": "iter/sec",
            "range": "stddev: 0.00011958435232573575",
            "extra": "mean: 1.98356076321245 msec\nrounds: 473"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_tool_no_embedding[noop]",
            "value": 445.9762322339673,
            "unit": "iter/sec",
            "range": "stddev: 0.00009454762839845046",
            "extra": "mean: 2.2422719591823035 msec\nrounds: 392"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_tool_no_embedding[resolver_on]",
            "value": 442.4750016629969,
            "unit": "iter/sec",
            "range": "stddev: 0.00009076277119986646",
            "extra": "mean: 2.260014681601452 msec\nrounds: 424"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_tool_no_embedding[consolidator_on]",
            "value": 447.7657061237023,
            "unit": "iter/sec",
            "range": "stddev: 0.00017329164703144237",
            "extra": "mean: 2.2333108282386736 msec\nrounds: 425"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_tool_no_embedding[fully_wired]",
            "value": 444.02303205125946,
            "unit": "iter/sec",
            "range": "stddev: 0.00009909086159351888",
            "extra": "mean: 2.2521354250032615 msec\nrounds: 440"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_no_embedding_direct[noop]",
            "value": 498.7984544229942,
            "unit": "iter/sec",
            "range": "stddev: 0.00016674038925180035",
            "extra": "mean: 2.0048177598240384 msec\nrounds: 458"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_no_embedding_direct[resolver_on]",
            "value": 503.9780757205264,
            "unit": "iter/sec",
            "range": "stddev: 0.00009841246847216553",
            "extra": "mean: 1.9842132985057372 msec\nrounds: 469"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_no_embedding_direct[consolidator_on]",
            "value": 498.9532882804029,
            "unit": "iter/sec",
            "range": "stddev: 0.00009553185276549552",
            "extra": "mean: 2.0041956301088004 msec\nrounds: 465"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_recall_no_embedding_direct[fully_wired]",
            "value": 500.426468313231,
            "unit": "iter/sec",
            "range": "stddev: 0.00008733721358815509",
            "extra": "mean: 1.998295580508887 msec\nrounds: 472"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_tool[noop]",
            "value": 212.4650994084017,
            "unit": "iter/sec",
            "range": "stddev: 0.00029719477714978173",
            "extra": "mean: 4.706655364972644 msec\nrounds: 137"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_tool[resolver_on]",
            "value": 212.17440140454374,
            "unit": "iter/sec",
            "range": "stddev: 0.0002696974616322704",
            "extra": "mean: 4.713103905938885 msec\nrounds: 202"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_tool[consolidator_on]",
            "value": 213.72564866922477,
            "unit": "iter/sec",
            "range": "stddev: 0.0002537230453897514",
            "extra": "mean: 4.678895613261948 msec\nrounds: 181"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_tool[fully_wired]",
            "value": 211.6771711462981,
            "unit": "iter/sec",
            "range": "stddev: 0.0003373977317390504",
            "extra": "mean: 4.724174999999703 msec\nrounds: 198"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_direct[noop]",
            "value": 226.32102564239725,
            "unit": "iter/sec",
            "range": "stddev: 0.000296291906090678",
            "extra": "mean: 4.418502422218909 msec\nrounds: 225"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_direct[resolver_on]",
            "value": 227.7605934965571,
            "unit": "iter/sec",
            "range": "stddev: 0.00030336001705031106",
            "extra": "mean: 4.390575141415392 msec\nrounds: 198"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_direct[consolidator_on]",
            "value": 227.31764546895343,
            "unit": "iter/sec",
            "range": "stddev: 0.0002681327085210247",
            "extra": "mean: 4.399130555558116 msec\nrounds: 243"
          },
          {
            "name": "benchmarks/adapters/test_mcp_adapter.py::test_mcp_forget_direct[fully_wired]",
            "value": 226.93895594801572,
            "unit": "iter/sec",
            "range": "stddev: 0.0002861960370168702",
            "extra": "mean: 4.406471316582012 msec\nrounds: 199"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_adapter[noop]",
            "value": 345.6459307390019,
            "unit": "iter/sec",
            "range": "stddev: 0.00034996989516052974",
            "extra": "mean: 2.8931340168303685 msec\nrounds: 297"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_adapter[resolver_on]",
            "value": 353.6712922219559,
            "unit": "iter/sec",
            "range": "stddev: 0.00011087443235665743",
            "extra": "mean: 2.82748422615094 msec\nrounds: 283"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_adapter[consolidator_on]",
            "value": 345.3209581588102,
            "unit": "iter/sec",
            "range": "stddev: 0.00009818627021561324",
            "extra": "mean: 2.89585667007245 msec\nrounds: 294"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_adapter[fully_wired]",
            "value": 352.7214507339497,
            "unit": "iter/sec",
            "range": "stddev: 0.00012722908852622316",
            "extra": "mean: 2.8350983415360202 msec\nrounds: 325"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_direct[noop]",
            "value": 380.63587156601767,
            "unit": "iter/sec",
            "range": "stddev: 0.00012314789764678507",
            "extra": "mean: 2.627182761009322 msec\nrounds: 318"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_direct[resolver_on]",
            "value": 377.4383696087345,
            "unit": "iter/sec",
            "range": "stddev: 0.0002452038269923978",
            "extra": "mean: 2.6494391681392493 msec\nrounds: 339"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_direct[consolidator_on]",
            "value": 364.5601428855416,
            "unit": "iter/sec",
            "range": "stddev: 0.00024814046001541936",
            "extra": "mean: 2.7430316218467223 msec\nrounds: 357"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_add_items_direct[fully_wired]",
            "value": 369.7253310299644,
            "unit": "iter/sec",
            "range": "stddev: 0.00015742753604525245",
            "extra": "mean: 2.7047105406985352 msec\nrounds: 344"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_adapter[noop]",
            "value": 453.1432560812831,
            "unit": "iter/sec",
            "range": "stddev: 0.00010809149562658028",
            "extra": "mean: 2.2068076410269337 msec\nrounds: 390"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_adapter[resolver_on]",
            "value": 463.34906799101674,
            "unit": "iter/sec",
            "range": "stddev: 0.00010055987624929088",
            "extra": "mean: 2.158200089482834 msec\nrounds: 447"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_adapter[consolidator_on]",
            "value": 459.13607301378596,
            "unit": "iter/sec",
            "range": "stddev: 0.00011388239976367365",
            "extra": "mean: 2.1780035566274796 msec\nrounds: 415"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_adapter[fully_wired]",
            "value": 456.5309634728851,
            "unit": "iter/sec",
            "range": "stddev: 0.00008783036755338611",
            "extra": "mean: 2.1904319312602185 msec\nrounds: 451"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_direct[noop]",
            "value": 500.6808835287892,
            "unit": "iter/sec",
            "range": "stddev: 0.00015008758275771115",
            "extra": "mean: 1.9972801696602023 msec\nrounds: 501"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_direct[resolver_on]",
            "value": 504.90500870913246,
            "unit": "iter/sec",
            "range": "stddev: 0.0000812257127555504",
            "extra": "mean: 1.9805705682275845 msec\nrounds: 491"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_direct[consolidator_on]",
            "value": 493.4865097857816,
            "unit": "iter/sec",
            "range": "stddev: 0.00017442082953535848",
            "extra": "mean: 2.0263978450679265 msec\nrounds: 497"
          },
          {
            "name": "benchmarks/adapters/test_openai_agents_adapter.py::test_openai_agents_get_items_direct[fully_wired]",
            "value": 513.8042401468063,
            "unit": "iter/sec",
            "range": "stddev: 0.00014208369944640015",
            "extra": "mean: 1.9462665386223281 msec\nrounds: 479"
          },
          {
            "name": "benchmarks/lifecycle/test_erase_all.py::test_erase_all_throughput[noop]",
            "value": 34.87015887122858,
            "unit": "iter/sec",
            "range": "stddev: 0.0008016888425521029",
            "extra": "mean: 28.677816000003986 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_erase_all.py::test_erase_all_throughput[resolver_on]",
            "value": 35.280178739001805,
            "unit": "iter/sec",
            "range": "stddev: 0.0019497229485044758",
            "extra": "mean: 28.34452759998385 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_erase_all.py::test_erase_all_throughput[consolidator_on]",
            "value": 34.52131345199277,
            "unit": "iter/sec",
            "range": "stddev: 0.0008893464969666093",
            "extra": "mean: 28.9676116000237 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_erase_all.py::test_erase_all_throughput[fully_wired]",
            "value": 31.824758317712245,
            "unit": "iter/sec",
            "range": "stddev: 0.0060848058293658915",
            "extra": "mean: 31.422076799981365 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_export_throughput_100[noop]",
            "value": 21.170298415142888,
            "unit": "iter/sec",
            "range": "stddev: 0.00244805979108266",
            "extra": "mean: 47.2359897999695 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_export_throughput_100[resolver_on]",
            "value": 21.829967015626522,
            "unit": "iter/sec",
            "range": "stddev: 0.0016304600364546774",
            "extra": "mean: 45.80858959998295 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_export_throughput_100[consolidator_on]",
            "value": 11.111970066396745,
            "unit": "iter/sec",
            "range": "stddev: 0.0031466361563303844",
            "extra": "mean: 89.99304300000404 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_export_throughput_100[fully_wired]",
            "value": 11.064331902620069,
            "unit": "iter/sec",
            "range": "stddev: 0.001422923880625135",
            "extra": "mean: 90.3805136000301 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_import_throughput_100[noop]",
            "value": 3.605620947874249,
            "unit": "iter/sec",
            "range": "stddev: 0.002410822466303537",
            "extra": "mean: 277.34473880000223 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_import_throughput_100[resolver_on]",
            "value": 3.5901816596593226,
            "unit": "iter/sec",
            "range": "stddev: 0.0012664928662547612",
            "extra": "mean: 278.5374375999936 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_import_throughput_100[consolidator_on]",
            "value": 1.5556424291970075,
            "unit": "iter/sec",
            "range": "stddev: 0.019561452296710576",
            "extra": "mean: 642.8212430000258 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_export_import.py::test_import_throughput_100[fully_wired]",
            "value": 1.5877082563418212,
            "unit": "iter/sec",
            "range": "stddev: 0.006131590163881885",
            "extra": "mean: 629.8386344000392 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_forget.py::test_forget_latency[noop]",
            "value": 574.7548419147121,
            "unit": "iter/sec",
            "range": "stddev: 0.00024235286961529922",
            "extra": "mean: 1.7398722499990527 msec\nrounds: 20"
          },
          {
            "name": "benchmarks/lifecycle/test_forget.py::test_forget_latency[resolver_on]",
            "value": 579.103747765866,
            "unit": "iter/sec",
            "range": "stddev: 0.00021676522688256512",
            "extra": "mean: 1.7268063000074108 msec\nrounds: 20"
          },
          {
            "name": "benchmarks/lifecycle/test_forget.py::test_forget_latency[consolidator_on]",
            "value": 568.7163307533223,
            "unit": "iter/sec",
            "range": "stddev: 0.00023938424734007554",
            "extra": "mean: 1.7583458499871085 msec\nrounds: 20"
          },
          {
            "name": "benchmarks/lifecycle/test_forget.py::test_forget_latency[fully_wired]",
            "value": 570.055779385648,
            "unit": "iter/sec",
            "range": "stddev: 0.00021648968819179882",
            "extra": "mean: 1.7542143000071064 msec\nrounds: 20"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[noop-10rows]",
            "value": 183.1563219053889,
            "unit": "iter/sec",
            "range": "stddev: 0.0005209318140608122",
            "extra": "mean: 5.459817000019029 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[noop-100rows]",
            "value": 41.86999223377666,
            "unit": "iter/sec",
            "range": "stddev: 0.005779302052707192",
            "extra": "mean: 23.883453200005533 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[resolver_on-10rows]",
            "value": 196.9195946564685,
            "unit": "iter/sec",
            "range": "stddev: 0.000544791412407211",
            "extra": "mean: 5.078214800028036 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[resolver_on-100rows]",
            "value": 40.85988617119645,
            "unit": "iter/sec",
            "range": "stddev: 0.006671657394379129",
            "extra": "mean: 24.473881200015057 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[consolidator_on-10rows]",
            "value": 176.17616263293127,
            "unit": "iter/sec",
            "range": "stddev: 0.0005504631470777212",
            "extra": "mean: 5.6761367999797585 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[consolidator_on-100rows]",
            "value": 43.91268138960267,
            "unit": "iter/sec",
            "range": "stddev: 0.005501133118668414",
            "extra": "mean: 22.772464999980002 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[fully_wired-10rows]",
            "value": 182.41517028600884,
            "unit": "iter/sec",
            "range": "stddev: 0.0005289836833629971",
            "extra": "mean: 5.482000200049697 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_purge_expired.py::test_purge_expired_throughput_pr[fully_wired-100rows]",
            "value": 42.83523398124057,
            "unit": "iter/sec",
            "range": "stddev: 0.004299741927437438",
            "extra": "mean: 23.345267599984254 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[noop-10facts]",
            "value": 197.18875096526222,
            "unit": "iter/sec",
            "range": "stddev: 0.000098784405626758",
            "extra": "mean: 5.071283200004473 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[noop-50facts]",
            "value": 48.100109642336406,
            "unit": "iter/sec",
            "range": "stddev: 0.00027380142726737887",
            "extra": "mean: 20.78997339997386 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[resolver_on-10facts]",
            "value": 203.32881808005865,
            "unit": "iter/sec",
            "range": "stddev: 0.00006117477800905819",
            "extra": "mean: 4.918141999951331 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[resolver_on-50facts]",
            "value": 47.999115587594,
            "unit": "iter/sec",
            "range": "stddev: 0.00007216818654516444",
            "extra": "mean: 20.83371719995739 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[consolidator_on-10facts]",
            "value": 197.45348973117058,
            "unit": "iter/sec",
            "range": "stddev: 0.00010340847911689348",
            "extra": "mean: 5.064483800015296 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[consolidator_on-50facts]",
            "value": 46.569482799934136,
            "unit": "iter/sec",
            "range": "stddev: 0.0005421915306094843",
            "extra": "mean: 21.4732898000193 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[fully_wired-10facts]",
            "value": 130.86148549930056,
            "unit": "iter/sec",
            "range": "stddev: 0.0002479151854849249",
            "extra": "mean: 7.641667799998686 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/lifecycle/test_reconcile.py::test_reconcile_throughput_pr[fully_wired-50facts]",
            "value": 41.63789072040864,
            "unit": "iter/sec",
            "range": "stddev: 0.00029151058767138967",
            "extra": "mean: 24.016586399989137 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_approx_recall.py::test_approx_vs_exact_recall_1k",
            "value": 5.502409408328975,
            "unit": "iter/sec",
            "range": "stddev: 0.0009321942993515578",
            "extra": "mean: 181.73856683334103 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_approx_recall.py::test_approx_vs_exact_latency_ratio_1k",
            "value": 5.4992336419226495,
            "unit": "iter/sec",
            "range": "stddev: 0.0005094538923227213",
            "extra": "mean: 181.84351949999686 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_chunk_vs_parent_search.py::test_parent_search",
            "value": 64.79185791708623,
            "unit": "iter/sec",
            "range": "stddev: 0.00022322451286518194",
            "extra": "mean: 15.43403804347908 msec\nrounds: 46"
          },
          {
            "name": "benchmarks/read/test_chunk_vs_parent_search.py::test_chunk_search",
            "value": 78.89543576742923,
            "unit": "iter/sec",
            "range": "stddev: 0.000270696738036181",
            "extra": "mean: 12.67500445713787 msec\nrounds: 35"
          },
          {
            "name": "benchmarks/read/test_context_card.py::test_context_card_no_long_term",
            "value": 199.11084487923233,
            "unit": "iter/sec",
            "range": "stddev: 0.00010864029333789288",
            "extra": "mean: 5.022328143936786 msec\nrounds: 132"
          },
          {
            "name": "benchmarks/read/test_context_card.py::test_context_card_with_long_term",
            "value": 62.187904080721786,
            "unit": "iter/sec",
            "range": "stddev: 0.00035298196849227795",
            "extra": "mean: 16.08029752380736 msec\nrounds: 42"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_no_filter",
            "value": 5.509351688794506,
            "unit": "iter/sec",
            "range": "stddev: 0.004071973648422172",
            "extra": "mean: 181.5095598333111 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_exact_50pct",
            "value": 5.335297381974926,
            "unit": "iter/sec",
            "range": "stddev: 0.0006127919780308444",
            "extra": "mean: 187.43097683335463 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_exact_10pct",
            "value": 5.338673045640468,
            "unit": "iter/sec",
            "range": "stddev: 0.0016096117125115792",
            "extra": "mean: 187.31246350000674 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_exact_1pct",
            "value": 5.37307760630136,
            "unit": "iter/sec",
            "range": "stddev: 0.0005441312502192167",
            "extra": "mean: 186.1130758333426 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_exact_01pct",
            "value": 5.462728932372201,
            "unit": "iter/sec",
            "range": "stddev: 0.0004895414184160446",
            "extra": "mean: 183.0586895999886 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_not_50pct",
            "value": 5.290762690406992,
            "unit": "iter/sec",
            "range": "stddev: 0.0013780391209772167",
            "extra": "mean: 189.00866633333635 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_not_10pct",
            "value": 5.247810071146388,
            "unit": "iter/sec",
            "range": "stddev: 0.000882713239666322",
            "extra": "mean: 190.5556768333175 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_not_1pct",
            "value": 5.2543450215042,
            "unit": "iter/sec",
            "range": "stddev: 0.0008512321671177929",
            "extra": "mean: 190.31867833333158 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_not_01pct",
            "value": 5.23529106952798,
            "unit": "iter/sec",
            "range": "stddev: 0.0003828284585692421",
            "extra": "mean: 191.01134716663637 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_50pct",
            "value": 7.089492593348114,
            "unit": "iter/sec",
            "range": "stddev: 0.0010115102911476075",
            "extra": "mean: 141.05381828570833 msec\nrounds: 7"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_10pct",
            "value": 27.370975112393616,
            "unit": "iter/sec",
            "range": "stddev: 0.0004607656575910467",
            "extra": "mean: 36.53505203573104 msec\nrounds: 28"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_1pct",
            "value": 110.5639715065902,
            "unit": "iter/sec",
            "range": "stddev: 0.0001612598831134705",
            "extra": "mean: 9.044537622641338 msec\nrounds: 106"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_01pct",
            "value": 317.89813454643826,
            "unit": "iter/sec",
            "range": "stddev: 0.0001027594437265704",
            "extra": "mean: 3.1456617429565976 msec\nrounds: 284"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_any_50pct",
            "value": 5.517569759695184,
            "unit": "iter/sec",
            "range": "stddev: 0.0006799587410897228",
            "extra": "mean: 181.23921283330446 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_any_10pct",
            "value": 5.580101564655618,
            "unit": "iter/sec",
            "range": "stddev: 0.001001981100447207",
            "extra": "mean: 179.20820766668535 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_any_1pct",
            "value": 5.638051424816348,
            "unit": "iter/sec",
            "range": "stddev: 0.0005627556165481869",
            "extra": "mean: 177.36624316664043 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_filter_selectivity.py::test_selectivity_array_contains_any_01pct",
            "value": 5.756287645762083,
            "unit": "iter/sec",
            "range": "stddev: 0.0005642806800375859",
            "extra": "mean: 173.7230766666471 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_get_messages.py::test_get_messages_range[full]",
            "value": 2.3892130988942846,
            "unit": "iter/sec",
            "range": "stddev: 0.0011963625173716614",
            "extra": "mean: 418.54784760002985 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_get_messages.py::test_get_messages_range[first_10]",
            "value": 2.4058908337148175,
            "unit": "iter/sec",
            "range": "stddev: 0.0010945115616817639",
            "extra": "mean: 415.6464566000068 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_get_messages.py::test_get_messages_range[last_10]",
            "value": 2.3713395395805628,
            "unit": "iter/sec",
            "range": "stddev: 0.002773932764453155",
            "extra": "mean: 421.7025791999731 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_get_messages.py::test_get_messages_range[middle_200]",
            "value": 2.400563777588672,
            "unit": "iter/sec",
            "range": "stddev: 0.006669744491955505",
            "extra": "mean: 416.5688115999501 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_get_summary.py::test_get_summary_10_turns",
            "value": 209.5155724447231,
            "unit": "iter/sec",
            "range": "stddev: 0.00010897188056043514",
            "extra": "mean: 4.772914911915829 msec\nrounds: 193"
          },
          {
            "name": "benchmarks/read/test_get_summary.py::test_get_summary_100_turns",
            "value": 25.182757262958376,
            "unit": "iter/sec",
            "range": "stddev: 0.000298905960687447",
            "extra": "mean: 39.70971047999228 msec\nrounds: 25"
          },
          {
            "name": "benchmarks/read/test_get_summary.py::test_get_summary_1000_turns",
            "value": 2.4209677241122627,
            "unit": "iter/sec",
            "range": "stddev: 0.0031197210075371057",
            "extra": "mean: 413.0579644000363 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_get_summary.py::test_get_summary_with_token_budget",
            "value": 2.3446000032256866,
            "unit": "iter/sec",
            "range": "stddev: 0.03275540194638989",
            "extra": "mean: 426.5119843999855 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_hybrid_rrf.py::test_vector_only_search",
            "value": 41.303873861165535,
            "unit": "iter/sec",
            "range": "stddev: 0.0002832964454365724",
            "extra": "mean: 24.21080413331917 msec\nrounds: 30"
          },
          {
            "name": "benchmarks/read/test_hybrid_rrf.py::test_hybrid_rrf_search",
            "value": 31.47059156614956,
            "unit": "iter/sec",
            "range": "stddev: 0.00021614387763715866",
            "extra": "mean: 31.775697571431138 msec\nrounds: 28"
          },
          {
            "name": "benchmarks/read/test_hybrid_rrf.py::test_hybrid_overhead_inline",
            "value": 31.651535991127407,
            "unit": "iter/sec",
            "range": "stddev: 0.0004959588312578544",
            "extra": "mean: 31.59404334375182 msec\nrounds: 32"
          },
          {
            "name": "benchmarks/read/test_list_all_offset.py::test_list_all_offset[offset_0]",
            "value": 44.48148620989907,
            "unit": "iter/sec",
            "range": "stddev: 0.00026793599711542133",
            "extra": "mean: 22.48126322221348 msec\nrounds: 27"
          },
          {
            "name": "benchmarks/read/test_list_all_offset.py::test_list_all_offset[offset_100]",
            "value": 4.530868550990564,
            "unit": "iter/sec",
            "range": "stddev: 0.00039756864572988054",
            "extra": "mean: 220.70823480000854 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_list_all_offset.py::test_list_all_offset[offset_500]",
            "value": 4.54371207318062,
            "unit": "iter/sec",
            "range": "stddev: 0.0003212665294557707",
            "extra": "mean: 220.08436800001618 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_list_all_offset.py::test_list_all_offset[offset_1000]",
            "value": 4.545450520663971,
            "unit": "iter/sec",
            "range": "stddev: 0.0007011741347360444",
            "extra": "mean: 220.0001948000363 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/read/test_metadata_filters.py::test_filter_exact_match",
            "value": 40.832847723023534,
            "unit": "iter/sec",
            "range": "stddev: 0.0002565094539539142",
            "extra": "mean: 24.490087166664885 msec\nrounds: 30"
          },
          {
            "name": "benchmarks/read/test_metadata_filters.py::test_filter_not",
            "value": 39.58807592687056,
            "unit": "iter/sec",
            "range": "stddev: 0.001569221844452379",
            "extra": "mean: 25.26013140540751 msec\nrounds: 37"
          },
          {
            "name": "benchmarks/read/test_metadata_filters.py::test_filter_array_contains",
            "value": 84.50855728123233,
            "unit": "iter/sec",
            "range": "stddev: 0.00022274996977310194",
            "extra": "mean: 11.833121191172909 msec\nrounds: 68"
          },
          {
            "name": "benchmarks/read/test_metadata_filters.py::test_filter_array_contains_any",
            "value": 42.123034224256585,
            "unit": "iter/sec",
            "range": "stddev: 0.0002967895748064684",
            "extra": "mean: 23.73998023684982 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/read/test_metadata_filters.py::test_filter_none_baseline",
            "value": 41.639411047433185,
            "unit": "iter/sec",
            "range": "stddev: 0.000405338000860817",
            "extra": "mean: 24.015709512818482 msec\nrounds: 39"
          },
          {
            "name": "benchmarks/read/test_multi_type_search.py::test_multi_type_search_fanout[1type_facts]",
            "value": 102.37760712732717,
            "unit": "iter/sec",
            "range": "stddev: 0.00019133568789219776",
            "extra": "mean: 9.767761017859097 msec\nrounds: 56"
          },
          {
            "name": "benchmarks/read/test_multi_type_search.py::test_multi_type_search_fanout[2types]",
            "value": 51.8972243639025,
            "unit": "iter/sec",
            "range": "stddev: 0.0003037285415167673",
            "extra": "mean: 19.26885324324893 msec\nrounds: 37"
          },
          {
            "name": "benchmarks/read/test_multi_type_search.py::test_multi_type_search_fanout[3types]",
            "value": 34.331430663380246,
            "unit": "iter/sec",
            "range": "stddev: 0.00038578440923559525",
            "extra": "mean: 29.127827785710483 msec\nrounds: 28"
          },
          {
            "name": "benchmarks/read/test_multi_type_search.py::test_multi_type_search_fanout[5types_all]",
            "value": 20.50688246199567,
            "unit": "iter/sec",
            "range": "stddev: 0.000979330822980072",
            "extra": "mean: 48.764116235280895 msec\nrounds: 17"
          },
          {
            "name": "benchmarks/read/test_search_corpus_size.py::test_search_corpus_small",
            "value": 129.17238484054383,
            "unit": "iter/sec",
            "range": "stddev: 0.00017467284042358165",
            "extra": "mean: 7.741592765624361 msec\nrounds: 64"
          },
          {
            "name": "benchmarks/read/test_search_corpus_size.py::test_search_corpus_medium",
            "value": 41.85359660041745,
            "unit": "iter/sec",
            "range": "stddev: 0.0010501902018827264",
            "extra": "mean: 23.892809249994684 msec\nrounds: 40"
          },
          {
            "name": "benchmarks/read/test_search_corpus_size.py::test_search_corpus_large",
            "value": 5.523880123205866,
            "unit": "iter/sec",
            "range": "stddev: 0.0008448242365140369",
            "extra": "mean: 181.03216900000993 msec\nrounds: 6"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode[DEFAULT]",
            "value": 41.802604297104196,
            "unit": "iter/sec",
            "range": "stddev: 0.0003474389737972811",
            "extra": "mean: 23.921954548398155 msec\nrounds: 31"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode[EXACT]",
            "value": 42.013343353847965,
            "unit": "iter/sec",
            "range": "stddev: 0.0002905277583767828",
            "extra": "mean: 23.80196195236652 msec\nrounds: 42"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode[APPROX]",
            "value": 39.62644060958792,
            "unit": "iter/sec",
            "range": "stddev: 0.00024997411348993856",
            "extra": "mean: 25.23567558974859 msec\nrounds: 39"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_metric[COSINE]",
            "value": 41.387871610108526,
            "unit": "iter/sec",
            "range": "stddev: 0.00028836378433006675",
            "extra": "mean: 24.161667684205366 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_metric[EUCLIDEAN]",
            "value": 41.89600165249028,
            "unit": "iter/sec",
            "range": "stddev: 0.0005172385321510524",
            "extra": "mean: 23.868626135128114 msec\nrounds: 37"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_metric[DOT]",
            "value": 41.951730648503926,
            "unit": "iter/sec",
            "range": "stddev: 0.00019658345756752224",
            "extra": "mean: 23.836918871800147 msec\nrounds: 39"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_metric[MANHATTAN]",
            "value": 41.7912750516247,
            "unit": "iter/sec",
            "range": "stddev: 0.0006846548861538849",
            "extra": "mean: 23.928439578948034 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[COSINE-DEFAULT]",
            "value": 41.50996209924001,
            "unit": "iter/sec",
            "range": "stddev: 0.00025598920824507465",
            "extra": "mean: 24.090602578948356 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[COSINE-EXACT]",
            "value": 41.55822287531884,
            "unit": "iter/sec",
            "range": "stddev: 0.0002023117197852916",
            "extra": "mean: 24.062626619048558 msec\nrounds: 42"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[COSINE-APPROX]",
            "value": 39.386605090867555,
            "unit": "iter/sec",
            "range": "stddev: 0.0003019955036294243",
            "extra": "mean: 25.38934233333725 msec\nrounds: 39"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[EUCLIDEAN-DEFAULT]",
            "value": 42.38151772437296,
            "unit": "iter/sec",
            "range": "stddev: 0.0003397954341192034",
            "extra": "mean: 23.595190868422236 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[EUCLIDEAN-EXACT]",
            "value": 42.29600311869781,
            "unit": "iter/sec",
            "range": "stddev: 0.00030480385434901534",
            "extra": "mean: 23.64289593022868 msec\nrounds: 43"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[EUCLIDEAN-APPROX]",
            "value": 40.05621630082149,
            "unit": "iter/sec",
            "range": "stddev: 0.00039851554228593323",
            "extra": "mean: 24.964914121943455 msec\nrounds: 41"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[DOT-DEFAULT]",
            "value": 42.72157705873929,
            "unit": "iter/sec",
            "range": "stddev: 0.00019947835862003607",
            "extra": "mean: 23.40737558974163 msec\nrounds: 39"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[DOT-EXACT]",
            "value": 42.504220108111255,
            "unit": "iter/sec",
            "range": "stddev: 0.0002090569277031365",
            "extra": "mean: 23.527075604644864 msec\nrounds: 43"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[DOT-APPROX]",
            "value": 39.96076168987838,
            "unit": "iter/sec",
            "range": "stddev: 0.0009819957064796537",
            "extra": "mean: 25.02454802440087 msec\nrounds: 41"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[MANHATTAN-DEFAULT]",
            "value": 41.889168471773544,
            "unit": "iter/sec",
            "range": "stddev: 0.0008763743625283401",
            "extra": "mean: 23.872519710526998 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[MANHATTAN-EXACT]",
            "value": 41.66797210162237,
            "unit": "iter/sec",
            "range": "stddev: 0.0007629866022473296",
            "extra": "mean: 23.999248093023095 msec\nrounds: 43"
          },
          {
            "name": "benchmarks/read/test_search_modes.py::test_search_mode_metric_cross[MANHATTAN-APPROX]",
            "value": 39.61471198576257,
            "unit": "iter/sec",
            "range": "stddev: 0.0003049543528287982",
            "extra": "mean: 25.243147049999948 msec\nrounds: 40"
          },
          {
            "name": "benchmarks/read/test_vector_literal_cost.py::test_insert_literal_cost_attribution",
            "value": 280.6080600435427,
            "unit": "iter/sec",
            "range": "stddev: 0.00015049084108300278",
            "extra": "mean: 3.563689510004906 msec\nrounds: 100"
          },
          {
            "name": "benchmarks/read/test_vector_literal_cost.py::test_search_literal_cost_attribution",
            "value": 172.77105587060032,
            "unit": "iter/sec",
            "range": "stddev: 0.00015174848672116936",
            "extra": "mean: 5.788006532465521 msec\nrounds: 77"
          },
          {
            "name": "benchmarks/write/test_add_messages.py::test_benchmark_add_messages[1]",
            "value": 377.76700121033855,
            "unit": "iter/sec",
            "range": "stddev: 0.00017042983025539012",
            "extra": "mean: 2.647134336233899 msec\nrounds: 345"
          },
          {
            "name": "benchmarks/write/test_add_messages.py::test_benchmark_add_messages[10]",
            "value": 37.87015407254306,
            "unit": "iter/sec",
            "range": "stddev: 0.0006466525870635812",
            "extra": "mean: 26.406018789478033 msec\nrounds: 38"
          },
          {
            "name": "benchmarks/write/test_add_messages.py::test_benchmark_add_messages[100]",
            "value": 3.7631116037305454,
            "unit": "iter/sec",
            "range": "stddev: 0.00271681692804965",
            "extra": "mean: 265.73753459999807 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/write/test_add_messages.py::test_benchmark_add_messages[1000]",
            "value": 0.3735442340849513,
            "unit": "iter/sec",
            "range": "stddev: 0.023699996267855232",
            "extra": "mean: 2.677059123799995 sec\nrounds: 5"
          },
          {
            "name": "benchmarks/write/test_chunking.py::test_benchmark_chunking[200]",
            "value": 242.75812221203185,
            "unit": "iter/sec",
            "range": "stddev: 0.00027404627247667004",
            "extra": "mean: 4.119326640393814 msec\nrounds: 203"
          },
          {
            "name": "benchmarks/write/test_chunking.py::test_benchmark_chunking[1000]",
            "value": 236.2463657764997,
            "unit": "iter/sec",
            "range": "stddev: 0.0002630581753927597",
            "extra": "mean: 4.2328693468497525 msec\nrounds: 222"
          },
          {
            "name": "benchmarks/write/test_chunking.py::test_benchmark_chunking[5000]",
            "value": 30.236620262471572,
            "unit": "iter/sec",
            "range": "stddev: 0.0017869503511967798",
            "extra": "mean: 33.07247937499014 msec\nrounds: 32"
          },
          {
            "name": "benchmarks/write/test_chunking.py::test_benchmark_chunking[20000]",
            "value": 9.082943844590444,
            "unit": "iter/sec",
            "range": "stddev: 0.0025630843273676167",
            "extra": "mean: 110.09646400000292 msec\nrounds: 10"
          },
          {
            "name": "benchmarks/write/test_chunking.py::test_benchmark_chunking[60000]",
            "value": 3.1573523116916262,
            "unit": "iter/sec",
            "range": "stddev: 0.008444952250459431",
            "extra": "mean: 316.72106919997987 msec\nrounds: 5"
          },
          {
            "name": "benchmarks/write/test_dedup.py::test_benchmark_dedup_on",
            "value": 432.47681462019085,
            "unit": "iter/sec",
            "range": "stddev: 0.0001190400685323552",
            "extra": "mean: 2.3122626836729236 msec\nrounds: 98"
          },
          {
            "name": "benchmarks/write/test_dedup.py::test_benchmark_dedup_off",
            "value": 187.20604427836983,
            "unit": "iter/sec",
            "range": "stddev: 0.00023466099169046795",
            "extra": "mean: 5.341707869821926 msec\nrounds: 169"
          },
          {
            "name": "benchmarks/write/test_ingest_resolver.py::test_benchmark_resolver_off",
            "value": 353.56649036304646,
            "unit": "iter/sec",
            "range": "stddev: 0.00032098275793357824",
            "extra": "mean: 2.8283223304708196 msec\nrounds: 233"
          },
          {
            "name": "benchmarks/write/test_ingest_resolver.py::test_benchmark_resolver_on",
            "value": 52.698254991694164,
            "unit": "iter/sec",
            "range": "stddev: 0.003943209304165331",
            "extra": "mean: 18.975960402438584 msec\nrounds: 82"
          },
          {
            "name": "benchmarks/write/test_remember_all_types.py::test_remember_all_types[WorkingMemory]",
            "value": 370.92403201270866,
            "unit": "iter/sec",
            "range": "stddev: 0.00011529836756556348",
            "extra": "mean: 2.6959698312719134 msec\nrounds: 243"
          },
          {
            "name": "benchmarks/write/test_remember_all_types.py::test_remember_all_types[EpisodicMemory]",
            "value": 293.6164259523448,
            "unit": "iter/sec",
            "range": "stddev: 0.00016833798451535708",
            "extra": "mean: 3.4058040068994786 msec\nrounds: 145"
          },
          {
            "name": "benchmarks/write/test_remember_all_types.py::test_remember_all_types[SemanticFact]",
            "value": 291.8696724475336,
            "unit": "iter/sec",
            "range": "stddev: 0.000255706289857219",
            "extra": "mean: 3.426186734696664 msec\nrounds: 98"
          },
          {
            "name": "benchmarks/write/test_remember_all_types.py::test_remember_all_types[EntityProfile]",
            "value": 288.9278545550039,
            "unit": "iter/sec",
            "range": "stddev: 0.0003012164572675814",
            "extra": "mean: 3.4610716282103136 msec\nrounds: 156"
          },
          {
            "name": "benchmarks/write/test_remember_all_types.py::test_remember_all_types[ProceduralMemory]",
            "value": 292.0307083424832,
            "unit": "iter/sec",
            "range": "stddev: 0.0001384886194591776",
            "extra": "mean: 3.4242974161033635 msec\nrounds: 149"
          },
          {
            "name": "benchmarks/write/test_update.py::test_benchmark_short_update",
            "value": 249.35089026689772,
            "unit": "iter/sec",
            "range": "stddev: 0.00027470734177476405",
            "extra": "mean: 4.010412791907941 msec\nrounds: 173"
          },
          {
            "name": "benchmarks/write/test_update.py::test_benchmark_long_update",
            "value": 23.813767995118926,
            "unit": "iter/sec",
            "range": "stddev: 0.00817204223284823",
            "extra": "mean: 41.99251459092776 msec\nrounds: 22"
          }
        ]
      }
    ]
  }
}