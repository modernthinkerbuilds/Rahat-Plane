"""Turn-by-turn routing + context harness over the Fraser/Gemini doc.

Hermetic. Stubs google.genai (no LLM) and forces RAHAT_TEST_MODE=1.

For each user turn from the doc it computes the DETERMINISTIC routing facts
that decide whether the turn reaches Fraser, Kobe, or a canned handler:

  - dispatcher.match_route(msg)  → which Kobe fast-path regex claims it
  - dispatcher.dispatch(msg)     → the ACTUAL Kobe fast-path output (or None)
  - kobe._should_delegate(msg)   → 'fraser' / 'huberman' / None
  - composer.parse_request(msg)  → what Fraser would capture (min/kcal/prefs)

Then it predicts the owner and flags concerns. This is the no-LLM path
(a real production fallback) AND it shows what Kobe's dispatcher claims
even on the classifier path (because Kobe runs the dispatcher first).
"""
import os, sys, types

os.environ["RAHAT_TEST_MODE"] = "1"
os.environ.setdefault("RAHAT_VOICE", "neutral")
os.environ.setdefault("RAHAT_LEGACY_DISPATCH", "1")
os.environ.setdefault("GEMINI_API_KEY", "")

# Stub google.genai exactly like tests/conftest.py
if "google" not in sys.modules:
    g = types.ModuleType("google"); sys.modules["google"] = g
    ga = types.ModuleType("google.genai"); sys.modules["google.genai"] = ga
    class _StubClient:
        def __init__(self, *a, **k): pass
        class models:
            @staticmethod
            def list(): return []
            @staticmethod
            def generate_content(**k):
                return type("R", (), {"text": "[LLM-FALLBACK]", "usage_metadata": None})()
    ga.Client = _StubClient

from core import dispatcher
from agents.the_scientist import handler as kobe
from agents.fraser import composer

# Canned Kobe routes that return STATIC text (degrade a bespoke ask)
CANNED = {"post_recovery", "pre_fuel", "breathing_box", "breathing_715"}
# Kobe read/log routes (correct for Kobe to own)
KOBE_READ = {"weight_log", "hrv_log", "tier_set", "pace", "show_plan_this_week",
             "show_plan_next_week", "workout_today", "current_weight",
             "list_dislikes", "weekly_remaining", "last_week",
             "gym_wod_on_day", "show_day_workout", "gym_wod_relative", "slash"}

# (id, intent_label, message). Messages faithful to the doc; long pasted
# WODs trimmed to the operative sentence (routing keys off keywords).
TURNS = [
 ("T02","pain report + remember","So I had double unders on Friday. After i did them had a string pain behind my right lower side of the ankle. It got a little better on Saturday but i dont want to worsen it. Can you remember that and tailor the workout accordingly?"),
 ("T03","substitution Q","I did bench press on Friday, is this still OK or would you recommend something else?"),
 ("T04","optimize calorie burn","Can you optimize this for maximum calorie burn?"),
 ("T05","tweak previous WOD","I like the previous workout that you gave not this one. So just use the previous workout make small tweaks the one with the emoms to increase calorie burn and then rewrite the whole thing."),
 ("T06","stretch that burns","Did this yesterday. Haven't recovered and rested today, no walk either, can you give a good stretch routine that also burns calories"),
 ("T07","cooldown before sleep","Did a 10K this morning but haven't had any chance to stretch. Give me like a quick 10-minute stretch to cool down for the night before I sleep."),
 ("T08","cooldown adequacy","Today's WOD was back squat 5 5 5 3 3, starting at 60% for the five. Given this, is the mobility given here enough for cool down or do we recommend something else?"),
 ("T09","warmup + plan idea","I was planning to go for a zone 2 10k to burn 900 calories, but it's raining. Can you give me a good warmup, and maybe I am thinking to do a 1 mile cash in and 1 mile cash out after this to burn more calories, is that a good idea? I can go 90 minutes"),
 ("T10","pain resolved","My ankle is back to normal"),
 ("T11","prefer running","I'd rather run instead of row and bike"),
 ("T12","reduce mileage","Wait 4 mile towards the end is a lot, I'd rather do 1 mile cash in and one mile cash out"),
 ("T13","reorder + no row/bike","Should I do the run first and dynamic warm up after? Also I don't want rowing and biking today, what should I do?"),
 ("T14","life/stress triage","I haven't gone to the workout since my wife and I had a fight. Haven't worked out for the third day, have a ton of work, feel extremely stressed out. Need to take care of the toddler. What should I do?"),
 ("T15","day plan + workout","It's 12:30 pm, I am super hungry, stressed out with a mild headache - is there a way I can make this work?"),
 ("T16","time-box session","I'm okay with the one hour. Just make sure I burn many calories. Maybe like just a run in front and back, and then the workout may be useful, right? Help me out, please."),
 ("T17","clean-based design","So I like the clean based workout. I'll rather do the one mile run, warm up, then the clean for strength and then a high intensity amrap with thrusters and burpees and cleans and then a cash out of a one mile run. What do you think?"),
 ("T18","equipment advice","There is rain outside, do you recommend a treadmill or a rower or bike, my biking and rowing efficiency is pretty bad so tell me if that is still needed"),
 ("T19","rewrite whole WOD","Rewrite the whole WOD with weights warmup and everything"),
 ("T20","cooldown (typo)","I did this, this was amazing. I don't do cute cash out but burnt 800 active calories. Give me a good cool shoe before sleep. I couldn't cook down earlier"),
 ("T21","what to wear","I will be in JW Mariott in Austin and will be doing a 6:30 Am zone 2 10K run. How should I dress. Do I need to layer up with a compression and a tee on top"),
 ("T22","small gym workout","So do a small gym workout today and I would rather run tomorrow in the AM. So give me a small 30 to 35 minute gym workout that is good on the strength portion."),
 ("T23","fit in 30 min","Can this be done in 30 minutes?"),
 ("T24","cooldown after run","I ran 6.5 miles. Give me a cool down"),
 ("T25","design like prev format","I love the previous format the crossfit workout for my prvn format with the cleans, design a similar format and give it to me today. I have the crossfit 26.1 workout tomorrow."),
 ("T26","calorie target 75min","75 minutes, but I want to burn 700 to 750 calories because I haven't worked out a lot this week."),
 ("T27","two AMRAPs","Change that up into two different 7 Minute amraps or 17 minutes. I need to burn a lot of calories in 75 minutes."),
 ("T28","stretch 10 min","I ran a 10k, give me a stretch for 10 min"),
 ("T29","diet Q","I did eat a lot of carbs after run, what should my tomorrow's diet be, assuming it's a rest day"),
 ("T30","adjust focus chest/shoulder","I loved this format, I haven't done shoulder work and chest work can you adjust this with focus on chest and shoulder strength?"),
 ("T31","one hour same format","So I have only one hour today. I want to maximise calorie burn in one hour without increasing my heart rate too high. Use the same format, cash in, strength, then a WOD, then a cash out, warm up, and a cool down. Give me target rates."),
 ("T32","swap to back squats","I want back squats in the strength not chest and the shoulder press, can you rewrite and have back squat change the rest of the workout."),
 ("T33","drop 26.1 framing","I don't have any 26.1 tomorrow. Just give me what's appropriate for me. The whole setup might be a lot for an hour. I just want to make sure I complete within an hour."),
 ("T34","PRVN no-neck no-run","Give me a good PRVN style workout today. I want to not use my neck as much, no running as well - solid strength. but I do want to burn 750 calories in about 75 minutes"),
 ("T35","front squat + bench rft/emom","I don't have a sled, no back squats as I just did them, maybe front squats and bench press? Also how about splitting into a 10 min rft and 12 min Emom for the WOD?"),
 ("T36","no carry, normal pushups","Also, no farmers carry as I did that last week, and can I do normal pushups - lower reps instead of incline"),
 ("T37","calorie doubt","Are you sure this will burn 750 calories?"),
 ("T38","weekend split options","I am dead tired but also motivated to burn calories. I want to squats one day, upper body one day and a 10K zone 2 on another. Between Friday night, Saturday and Sunday. What are my options?"),
 ("T39","warmup+cooldown 800","Give me warmup and cool down and perhaps additions so that I burn 800 calories in 75 minutes"),
 ("T40","mobility warmup + rewrite","Since I have mobility issues, give me a good warmup and then rewrite the WOD"),
 ("T41","accessory advice","Are the big 3 enough? I have a feeling I need to open up body to protect myself while doing deadlifts"),
 ("T42","deadlift progression numbers","For the deadlift, give me a progression to build up weight and give me exact numbers. I am not feeling as strong today so feel free to lower it a bit"),
 ("T43","ramp from empty bar","How do I build up to 92.5 kg from empty bar"),
 ("T44","substitute no rope","I don't have a jump rope, what is the alternative? I don't like lateral hops"),
 ("T45","intense as DU","Give me something as intense as double unders"),
 ("T46","how about a run","How about a run?"),
 ("T47","cooldown + tmrw 10k","Haven't stretched / recovered after the workout, give me a stretch/recover routine for tonight, also note that I will run a 10k tomorrow morning"),
 ("T48","home DB short WOD","I ran a 7.5 to 8k. To compensate for the calories, I want to do bench dumbbell chest press and push-ups in a short WOD, 15 to 20 minutes, build strength. I only have 2x40-pound dumbbells at home."),
 ("T49","strength + cooldown only","Can we just do a strength portion with floor press and then push ups. Just the strength and then the cool down. I don't want to do any WOD in between because I already ran 7.5 to 8k."),
 ("T50","maximize DB strength","2x40 pound dumbbells is pretty challenging, I can get six to seven reps in a set. Maximize my strength with dumbbells and push-ups and give me the workout."),
 ("T51","order pushups first","Should I switch and start with push ups? That way I will warm up?"),
 ("T52","warmup for this WOD","Give me a good warm up for this WOD"),
 ("T53","weights from 1RM","Given my 1rm for power clean is 70, give me the weights"),
 ("T54","pasted WOD how to do","I am feeling weak but here is the WOD for today, how should I do it? Every 3:00 x 5 Sets: 6 Back Squats + 8 Jumping Lunges, starting at 70%."),
 ("T55","switch to emom","Should I switch to a 10 min EMOM and cash in cash out for today"),
 ("T56","emom unrealistic","The emom is not realistic"),
 ("T57","meta agents","Separately if I had a team of sync agents telling me the same instructions, with something like open claw, what would those agents be?"),
 ("T58","lunges count","If I cash in 400 and cash out 400 and do an 8 min Emom, how many lunges would you recommend"),
 ("T59","squats make it good","Wouldn't the squats with the WOD make it good today?"),
 ("T60","design chest 700 2-part","Based on my workout patterns, design a PRVN workout for me today. Focus on chest bench press and then have a WOD as well. 75 to 80 minutes and burn 700 calories. Break the metcon into two portions. I've already done deadlifts and back squats and box jumps this week, so don't have those, not even running."),
 ("T61","swap rows + rowing weak","In portion B I have already done lunges yesterday, can you swap something else, and I am pretty bad at rowing, should I substitute to maximize calorie burn or just lower to improve on rowing?"),
 ("T62","how increase burn","What are things that I can do to increase calorie burn"),
 ("T63","apply + rewrite","Apply these and rewrite the WOD for 75 minutes with weights to increase calorie burn and strength"),
 ("T64","keep prior movements","I like the previous Emom and amrap movements - will they burn less calories? I am ok increasing time but want to keep prior movements"),
 ("T65","sub db rows","I can substitute db rows for a high calories movement"),
 ("T66","no devils press alternatives","I don't like devils press. How about chin ups? Will that burn calories? Or farmers carry, or front squat?"),
 ("T67","elevation zone2","Is this high elevation for a zone 2 run?"),
 ("T68","cooldown HRV 30s","I haven't stretched after the workout this morning, give me a cool down, My HRV is in 30s"),
 ("T69","correction not run","I did not run today, it was only bench press and the WOD"),
 ("T70","cooldown raise HRV","I ran a 10K today at zone 2, I have not cooled down, give me a great stretch to cool down and increase HRV during sleep"),
 ("T71","prvn + 2 wods goal","Give me my usual PRVN format plus two wods. For the strength, give me a movement between deadlift and back squats, I want to burn 800 calories in 90 minutes. I'll run a 10K tomorrow keep that under consideration"),
 ("T72","sdhp calorie doubt","Wouldn't sumo deadlift high pull burn lower calories?"),
 ("T73","sdhp for strength","I meant Sdhp for strength"),
 ("T74","neck pain WOD 800","I have neck pain on my right, give me a WOD that can still burn 800 calories"),
 ("T75","postpartum resume plan","So this is week two of the baby. 3 to 4 hours sleep. I want to resume working out, maybe a 5K zone 2 run and two to three crossfit workouts. Recommend what is ideal and how I should resume gradually."),
 ("T76","swap to squats","Instead of the sumo deadlift high pull can I do back squats?"),
 ("T77","hip catch start today","I have a small catch at a weird place in my hip, I want to start off today, what can I do?"),
 ("T78","describe pain location","It's behind my hip on the crease line of my left glute, I can't describe the place correctly"),
 ("T79","bench is easy","How about doing the bench press for strength? That's easy, right?"),
 ("T80","pain only standing","I only feel it painful randomly only when I am standing, morning when on bed or sitting"),
 ("T81","alt for rows","I am doing step ups instead of bird dogs, what is the alternate for rows?"),
 ("T82","trx rows","How about TRX rows?"),
 ("T83","deadlift wod today","Give me the dead lift WOD for today"),
 ("T84","change metcon dup","I did the same Metcon earlier on the bench press day, I did 3 RFT, so change it"),
 ("T85","rewrite + warmup","Nice! Rewrite the WOD and give me a good warm up"),
 ("T86","pasted complex 800","Assuming I have to burn 800 calories in 75 minutes, give me target weights. Add a warm up, cool down and accessory core work. Every 2:00 x 5 Sets: Clean Grip Deadlift + Low Hang Power Clean."),
 ("T87","complex sequence Q","For the complex, should I complete deadlift and go to clean? Should I start at 60 or do 40, 50, 60, 60, 60?"),
 ("T88","everything normal","Everything is normal"),
 ("T89","box+db spec","What's the height of the box and weight of the dumbbell?"),
 ("T90","reduce box reps","I want to use 20 inch and only do 5 to allow for enough rest"),
 ("T91","late cooldown + tmrw","I worked out but was not able to do the cool down and it's been like three and a half hours, I had my dinner. Can you help me with a very good cool down? I have a 10k zone 2 run tomorrow as well."),
]

def predict(msg):
    name = dispatcher.match_route(msg)
    try:
        out = dispatcher.dispatch(msg)
    except Exception as e:
        out = f"<dispatch error: {type(e).__name__}>"
    deleg = kobe._should_delegate(msg)
    # Predicted owner under the no-LLM fallback (Miya→Kobe→dispatcher/deleg)
    if out is not None:
        if name in CANNED:
            owner = f"KOBE-CANNED({name})"; flag = "CONCERN: static block, not bespoke"
        elif name in KOBE_READ:
            owner = f"KOBE-READ({name})"; flag = "ok if a lookup; CONCERN if user wanted design"
        else:
            owner = f"KOBE({name})"; flag = ""
    else:
        if deleg == "fraser":
            owner = "FRASER (delegated)"; flag = "ok"
        elif deleg == "huberman":
            owner = "HUBERMAN (delegated)"; flag = "stub"
        else:
            owner = "KOBE-REASONER / classifier-dependent"
            flag = "CONCERN: no delegation match; Fraser only if Gemini classifier picks it"
    return name, out, deleg, owner, flag

print(f"{'ID':4} {'INTENT':26} {'ROUTE':18} {'DELEG':9} OWNER / CONCERN")
print("="*150)
counts = {}
for tid, label, msg in TURNS:
    name, out, deleg, owner, flag = predict(msg)
    bucket = ("FRASER" if owner.startswith("FRASER") else
              "KOBE-CANNED" if owner.startswith("KOBE-CANNED") else
              "KOBE-READ" if owner.startswith("KOBE-READ") else
              "KOBE-OTHER" if owner.startswith("KOBE(") else
              "HUBERMAN" if owner.startswith("HUBERMAN") else
              "CLASSIFIER-DEP")
    counts[bucket] = counts.get(bucket, 0) + 1
    print(f"{tid:4} {label[:26]:26} {str(name):18} {str(deleg or '-'):9} {owner}")
    if flag:
        print(f"{'':59}↳ {flag}")

print("\n" + "="*60)
print("BUCKET COUNTS (no-LLM fallback / dispatcher-claimed):")
for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
    print(f"  {k:18} {v}")
print(f"  TOTAL: {len(TURNS)} turns")

# Context capture check for a few design turns
print("\n" + "="*60)
print("FRASER parse_request capture (what the composer extracts):")
for tid in ("T26","T34","T48","T60","T74","T86"):
    msg = dict((t[0], t[2]) for t in TURNS)[tid]
    req = composer.parse_request(msg)
    print(f"  {tid}: minutes={req.minutes} kcal={req.kcal_target} prefs={req.preferences}")
