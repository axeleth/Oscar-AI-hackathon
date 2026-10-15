# Oscar AI – Autonomous Stripe Accounting AI

The name "Oscar" is inspired by the accountant in *The Office*. We're taking Oscar's job today... not really.

## What even is this?
I found an interesting hackathon hosted by Stripe and Briefcase titled *AI Fintech London Hackathon*. 
    
```
https://ukhackathons.com/events/stripe-x-briefcase-ai-fintech-london-hackathon 
```
I have very limited fintech (let along financial) knowledge, but I do know a thing or two about AI agents. I've worked on Multi-Agent Pathfinding in real world problems, with solo agents that perform a specific task in isolation, and everything in between. So why not give it a shot. 

The hackathon lasts for about 3 hours and is all about letting AI agents handle real money with a focus on **autonomy**, **policy**, and **auditability**.


### An agentic description of the hackathon and how to prep:

It’s a short, fast evening. After the briefing you get about three hours of building (6:00 to 9:00 pm), then finalists demo live. That’s too short to learn a new stack on the night, so the people who do well arrive with their setup working and spend the three hours on the idea and the demo.

**The hosts.** Briefcase is a London seed-stage startup building an AI-native accounting automation platform that uses agentic AI to take on tasks from bookkeeping to month-end close. Jan Stehlik is its CTO and co-founder. Earlybird led their round, and Akash Bajwa, the Earlybird principal who backed them, is one of your judges. Jan ran a similar fintech hackathon with Cursor earlier, so this is a recurring event and he takes it seriously. 
crunchbase
luma

**Fintech knowledge.** You need very little. The hint (autonomy, policy, auditability) points to one core question: how do you let an agent move money safely? That means spending limits, approval thresholds, allowlists of who can be paid, a human sign-off above a certain amount, and a log explaining why the agent did each thing. Those are agent design problems, and your background covers them.

The tooling. Almost everyone will use Stripe’s Agent Toolkit, which exposes Stripe’s payments, billing and customer APIs as tools an LLM agent can call, with Python and TypeScript SDKs plus an MCP server. Stripe also recommends restricted API keys so an agent only gets the access it needs. Everything runs in test mode, so no real money moves. 
ecommerceguide
stripe

Preparing beforehand:

- Create a Stripe account, get test keys, and run the toolkit with Claude end to end at least once.
- Build a starter repo: an agent loop, Stripe test mode, a basic UI, and an audit log panel. On the night you only swap in the idea.
- Have two or three ideas ready that fit the hint, such as an agent that pays supplier invoices within a spending policy and escalates anything unusual, or one that handles refunds and disputes with a full decision trail. Something close to Briefcase’s world (reconciliation, month-end) will land well with the judges.