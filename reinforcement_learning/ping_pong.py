""" Trains an agent with (stochastic) Policy Gradients on Pong. Uses OpenAI Gym. """
import numpy as np
import cPickle as pickle
import gym

# hyperparameters
H = 200            # number of hidden layer neurons
batch_size = 10    # every how many episodes to do a param update?
learning_rate = 1e-4
decay_rate = 0.99  # decay factor for RMSProp leaky sum of grad^2

gamma = 0.99       # discount factor for reward

resume = False     # resume from previous checkpoint?
render = False     # render one frame of the env if set.

# model initialization
# Input (6400,)
# 1 hidden fully connected layer with 200 nodes. (200, 6400)
# Ouput layer output a scaler value. (200,)
# Use a sigmod to map it to a probability for action 2.
D = 80 * 80        # input dim.: 80x80 grid
if resume:
    model = pickle.load(open('save.p', 'rb'))
else:
    model = {}
    model['W1'] = np.random.randn(H, D) / np.sqrt(D)  # Layer one (200, 6400)
    model['W2'] = np.random.randn(H) / np.sqrt(H)     # Output layer (200,)

grad_buffer = {k: np.zeros_like(v) for k, v in model.iteritems()}    # update buffers that add up gradients over a batch
rmsprop_cache = {k: np.zeros_like(v) for k, v in model.iteritems()}  # rmsprop memory


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))  # sigmoid "squashing" function to interval [0,1]

def prepro(I):
    """ prepro 210x160x3 uint8 frame into 6400 (80x80) 1D float vector """
    I = I[35:195]  # crop
    I = I[::2, ::2, 0]  # downsample by factor of 2
    I[I == 144] = 0  # erase background (background type 1)
    I[I == 109] = 0  # erase background (background type 2)
    I[I != 0] = 1  # everything else (paddles, ball) just set to 1
    return I.astype(np.float).ravel()


def discount_rewards(r):
    """ take 1D float array of rewards and compute discounted reward """
    discounted_r = np.zeros_like(r)
    running_add = 0
    for t in reversed(xrange(0, r.size)):
        if r[t] != 0: running_add = 0  # reset the sum, since this was a game boundary (pong specific!)
        running_add = running_add * gamma + r[t]
        discounted_r[t] = running_add
    return discounted_r


def policy_forward(x):
    """
        x is the input feature of shape (6400,)
        model['w1'] shape (200, 6400)
        model0'w2'] shape (200,)
        return p (probability): scalar
               h (hiddend states of 1st layer): (200,)
    """
    h = np.dot(model['W1'], x)
    h[h < 0] = 0  # ReLU nonlinearity
    logp = np.dot(model['W2'], h)
    p = sigmoid(logp)
    return p, h  # return probability of taking action 2, and hidden state


def policy_backward(eph, epdlogp):
    """
        backward pass. (eph is array of intermediate hidden states)
    """
    dW2 = np.dot(eph.T, epdlogp).ravel()
    dh = np.outer(epdlogp, model['W2'])
    dh[eph <= 0] = 0  # backpro prelu
    dW1 = np.dot(dh.T, epx)
    return {'W1': dW1, 'W2': dW2}


env = gym.make("Pong-v0")
observation = env.reset() # One image frame of the game. shape: (210, 160, 3)
prev_x = None             # Store the last image frame.
xs, hs, dlogps, drs = [], [], [], []
running_reward = None
reward_sum = 0
episode_number = 0
while True:
    if render: env.render()

    # Preprocess the observation.
    # Crop it to 80x80, flatten it to 6400. Set background pixels to 0. Otherwise set the pixel to 1.
    cur_x = prepro(observation)

    # Compute the difference of current frame and the last frame. This captures motion.
    x = cur_x - prev_x if prev_x is not None else np.zeros(D)
    prev_x = cur_x

    # Forward the policy network and sample an action from the returned probability
    aprob, h = policy_forward(x)
    action = 2 if np.random.uniform() < aprob else 3  # roll the dice!

    # record various intermediates (needed later for backprop)
    xs.append(x)  # observation
    hs.append(h)  # hidden state

    # For action 2, we want a true label set to 1
    # since aprob measures the probability of action 2.
    y = 1 if action == 2 else 0
    dlogps.append(y - aprob)

    # step the environment and get new measurements
    observation, reward, done, info = env.step(action)
    reward_sum += reward

    drs.append(reward)  # record reward (has to be done after we call step() to get reward for previous action)

    if done:  # an episode finished
        episode_number += 1

        # stack together all inputs, hidden states, action gradients, and rewards for this episode
        epx = np.vstack(xs)         # Shape: (# steps in the episode, 6400)
        eph = np.vstack(hs)         # (#steps, 200)
        epdlogp = np.vstack(dlogps) # (#steps, 1)
        epr = np.vstack(drs)        # (#steps, 1)
        xs, hs, dlogps, drs = [], [], [], []  # reset array memory

        # compute the discounted reward backwards through time
        discounted_epr = discount_rewards(epr)

        # standardize the rewards to be unit normal (helps control the gradient estimator variance)
        discounted_epr -= np.mean(discounted_epr)
        discounted_epr /= np.std(discounted_epr)

        epdlogp *= discounted_epr  # modulate the gradient with advantage (PG magic happens right here.)

        # grad contains the gradient for both W1 and W2
        grad = policy_backward(eph, epdlogp) # eph (#steps, 200), epdloop (#steps, 1)

        # Add all the gradient for steps in an episode
        for k in model:
            grad_buffer[k] += grad[k]  # accumulate grad over batch; grad_buffer['w1']:(200, 6400), grad_buffer['w1']:(200,)

        # perform rmsprop parameter update every batch_size episodes
        if episode_number % batch_size == 0:
            for k, v in model.iteritems():
                g = grad_buffer[k]  # gradient
                rmsprop_cache[k] = decay_rate * rmsprop_cache[k] + (1 - decay_rate) * g ** 2
                model[k] += learning_rate * g / (np.sqrt(rmsprop_cache[k]) + 1e-5)
                grad_buffer[k] = np.zeros_like(v)  # reset batch gradient buffer

        # boring book-keeping
        running_reward = reward_sum if running_reward is None else running_reward * 0.99 + reward_sum * 0.01
        print
        'resetting env. episode reward total was %f. running mean: %f' % (reward_sum, running_reward)
        if episode_number % 100 == 0: pickle.dump(model, open('save.p', 'wb'))
        reward_sum = 0
        observation = env.reset()  # reset env
        prev_x = None

    if reward != 0:  # Pong has either +1 or -1 reward exactly when game ends.
        print('ep %d: game finished, reward: %f' % (episode_number, reward)) + ('' if reward == -1 else ' !!!!!!!!')