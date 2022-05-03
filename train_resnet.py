from __future__ import print_function
import os
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
import torch.optim as optim
from torchvision import datasets, transforms
from torch.autograd import Variable
from preactresnet import PreActResNet18
from wideresnet import *
from resnet import * 
from mart import mart_loss
import numpy as np
import time
from utils import *
from utils_our import *
cifar10_mean=(0,0,0)
cifar10_std=(1.0,1.0,1.0)
mu = torch.tensor(cifar10_mean).view(3, 1, 1).cuda()
std = torch.tensor(cifar10_std).view(3, 1, 1).cuda()

#os.environ["CUDA_VISIBLE_DEVICES"]="0"
parser = argparse.ArgumentParser(description='PyTorch CIFAR MART Defense')
parser.add_argument('--batch-size', type=int, default=128, metavar='N',
                    help='input batch size for training (default: 128)')
parser.add_argument('--test-batch-size', type=int, default=100, metavar='N',
                    help='input batch size for testing (default: 100)')
parser.add_argument('--epochs', type=int, default=120, metavar='N',
                    help='number of epochs to train')
parser.add_argument('--weight-decay', '--wd', default=3.5e-3,
                    type=float, metavar='W')
parser.add_argument('--lr', type=float, default=0.01, metavar='LR',
                    help='learning rate')
parser.add_argument('--momentum', type=float, default=0.9, metavar='M',
                    help='SGD momentum')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='disables CUDA training')
parser.add_argument('--epsilon', default=0.031,
                    help='perturbation')
parser.add_argument('--num-steps', default=10,
                    help='perturb number of steps')
parser.add_argument('--step-size', default=0.007,
                    help='perturb step size')
parser.add_argument('--beta', default=5.0,
                    help='weight before kl (misclassified examples)')
parser.add_argument('--seed', type=int, default=1, metavar='S',
                    help='random seed (default: 1)')
parser.add_argument('--log-interval', type=int, default=100, metavar='N',
                    help='how many batches to wait before logging training status')
parser.add_argument('--model', default='PreActResNet18',
                    help='directory of model for saving checkpoint')
parser.add_argument('--save-freq', '-s', default=1, type=int, metavar='N',
                    help='save frequency')
parser.add_argument('--pgd-alpha', default=2, type=float)
parser.add_argument('--eval', action='store_true')
parser.add_argument('--restarts', default=1, type=int)
parser.add_argument('--attack-iters', default=10, type=int)
parser.add_argument('--norm', default='l_inf', type=str, choices=['l_inf', 'l_2'])
parser.add_argument('--reinitialize', default=0, type=int)
parser.add_argument('--initialize_type',default='zero',type=str,choices=['zero','one','random'])   
parser.add_argument('--gap',default=100,type=int)
parser.add_argument('--num_gaps',default=4,type=int)
parser.add_argument('--layer_wise',default=1,type=int)
parser.add_argument('--MetaStartEpoch',default=50,type=int)
parser.add_argument('--repeat',default=0,type=int)
parser.add_argument('--file_name', default=None, type=str)
parser.add_argument('--dataset', default='cifar10', type=str)
parser.add_argument('--train_mode_epoch',default=150,type=int)
parser.add_argument('--times',default=2,type=int)
parser.add_argument('--meta_loss',default='CE',choices=['kl','CE'])
parser.add_argument('--attack', default='pgd', type=str, choices=['pgd', 'fgsm', 'free', 'none'])

args = parser.parse_args()
print(args)
# settings
model_dir = args.model
if not os.path.exists(model_dir):
    os.makedirs(model_dir)
    
log_dir = './log'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
    
use_cuda = not args.no_cuda and torch.cuda.is_available()
torch.manual_seed(args.seed)
device = torch.device("cuda" if use_cuda else "cpu")
kwargs = {'num_workers': 10, 'pin_memory': True} if use_cuda else {}
torch.backends.cudnn.benchmark = True

# setup data loader
# transform_train = transforms.Compose([
#     transforms.RandomCrop(32, padding=4),
#     transforms.RandomHorizontalFlip(),
#     transforms.ToTensor(),
# ])
# transform_test = transforms.Compose([
#     transforms.ToTensor(),
# ])
# trainset = torchvision.datasets.CIFAR10(root='../data_attack/', train=True, download=True, transform=transform_train)
# train_loader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size, shuffle=True, num_workers=10)
# testset = torchvision.datasets.CIFAR10(root='../data_attack/', train=False, download=True, transform=transform_test)
# test_loader = torch.utils.data.DataLoader(testset, batch_size=args.test_batch_size, shuffle=False, num_workers=10)
transform_train = transforms.Compose([
    transforms.RandomCrop(32, padding=4),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
])
transform_test = transforms.Compose([
    transforms.ToTensor(),
])


class Batches():
    def __init__(self, dataset, batch_size, shuffle, set_random_choices=False, num_workers=0, drop_last=False):
        self.dataset = dataset
        self.batch_size = batch_size
        self.set_random_choices = set_random_choices
        self.dataloader = torch.utils.data.DataLoader(
            dataset, batch_size=batch_size, num_workers=num_workers, pin_memory=True, shuffle=shuffle, drop_last=drop_last
        )

    def __iter__(self):
        if self.set_random_choices:
            self.dataset.set_random_choices()
        return ({'input': x.to(device).float(), 'target': y.to(device).long()} for (x,y) in self.dataloader)

    def __len__(self):
        return len(self.dataloader)
try:
    if args.dataset=='cifar10':
        dataset = torch.load("cifar10_validation_split.pth")
    elif args.dataset=='cifar100':
        dataset=torch.load("cifar100_validation_split.pth")
except:
    print("Couldn't find a dataset with a validation split, did you run "
          "generate_validation.py?")
    
transforms = [Crop(32, 32), FlipLR()]
val_set = list(zip(transpose(dataset['val']['data']/255.), dataset['val']['labels']))
val_batches = Batches(val_set, args.batch_size, shuffle=False, num_workers=2)
train_set = list(zip(transpose(pad(dataset['train']['data'], 4)/255.),dataset['train']['labels']))
train_set_x = Transform(train_set, transforms)
train_batches = Batches(train_set_x, args.batch_size, shuffle=True, set_random_choices=True, num_workers=2)

test_set = list(zip(transpose(dataset['test']['data']/255.), dataset['test']['labels']))
test_batches = Batches(test_set, args.batch_size, shuffle=False, num_workers=2)




def train(args, model, device, train_loader, optimizer, epoch,Gadaptor):
    model.train()
    for batch_idx, batch in enumerate(train_loader):
        data, target = batch['input'].to(device), batch['target'].to(device)

        optimizer.zero_grad()

        # calculate robust loss
        loss = mart_loss(model=model,
                           x_natural=data,
                           y=target,
                           optimizer=optimizer,
                           step_size=args.step_size,
                           epsilon=args.epsilon,
                           perturb_steps=args.num_steps,
                           beta=args.beta)
        loss.backward()
        optimizer.step()
        model,optimizer=Gadaptor.take_step(epoch,model,optimizer,val_batches,train_batches,test_batches)

        # print progress
        if batch_idx % args.log_interval == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                epoch, batch_idx * len(data), len(train_loader.dataset),
                       100. * batch_idx / len(train_loader), loss.item()))
    return model,optimizer

def adjust_learning_rate(optimizer, epoch):
    """decrease the learning rate"""
    lr = args.lr
    if epoch >= 100:
        lr = args.lr * 0.001
    elif epoch >= 90:
        lr = args.lr * 0.01
    elif epoch >= 75:
        lr = args.lr * 0.1
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr
        
def _pgd_whitebox(model,
                  X,
                  y,
                  epsilon=args.epsilon,
                  num_steps=20,
                  step_size=0.003):
    out = model(X)
    err = (out.data.max(1)[1] != y.data).float().sum()
    X_pgd = Variable(X.data, requires_grad=True)

    random_noise = torch.FloatTensor(*X_pgd.shape).uniform_(-epsilon, epsilon).to(device)
    X_pgd = Variable(X_pgd.data + random_noise, requires_grad=True)

    for _ in range(num_steps):
        opt = optim.SGD([X_pgd], lr=1e-3)
        opt.zero_grad()

        with torch.enable_grad():
            loss = nn.CrossEntropyLoss()(model(X_pgd), y)
        loss.backward()
        eta = step_size * X_pgd.grad.data.sign()
        X_pgd = Variable(X_pgd.data + eta, requires_grad=True)
        eta = torch.clamp(X_pgd.data - X.data, -epsilon, epsilon)
        X_pgd = Variable(X.data + eta, requires_grad=True)
        X_pgd = Variable(torch.clamp(X_pgd, 0, 1.0), requires_grad=True)
    err_pgd = (model(X_pgd).data.max(1)[1] != y.data).float().sum()
    return err, err_pgd

def eval_adv_test_whitebox(model, device, test_loader):

    model.eval()
    robust_err_total = 0
    natural_err_total = 0

    for  batch in test_loader:
        #data, target = data.to(device), target.to(device)
        data,target=batch['input'].to(device), batch['target'].to(device)
        # pgd attack
        X, y = Variable(data, requires_grad=True), Variable(target)
        err_natural, err_robust = _pgd_whitebox(model, X, y)
        robust_err_total += err_robust
        natural_err_total += err_natural
    print('natural_acc: ', 1 - natural_err_total / len(test_loader.dataset))
    print('robust_acc: ', 1- robust_err_total / len(test_loader.dataset))
    return 1 - natural_err_total / len(test_loader.dataset), 1- robust_err_total / len(test_loader.dataset)


def main():

    model = PreActResNet18().to(device)
    model = nn.DataParallel(model).cuda()
    optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=args.momentum, weight_decay=args.weight_decay)   
    Gadaptor=GAdaptor(model,args,mu,std)
    natural_acc = []
    robust_acc = []
    best_robust_err=0.0

    for epoch in range(0, args.epochs):
        # adjust learning rate for SGD
        adjust_learning_rate(optimizer, epoch)
        
        start_time = time.time()

        # adversarial training
        model,optimizer=train(args, model, device, train_batches, optimizer, epoch,Gadaptor)


        print('================================================================')

        natural_err_total, robust_err_total = eval_adv_test_whitebox(model, device, test_batches)

        #if best_robust_err>robust_err_total:
        ##    torch.save(model.state_dict(),os.path.join(model_dir,'preRN18_best.pt'))
         #   best_robust_err=robust_err_total


        print('using time:', time.time()-start_time)
        #torch.save(model.state_dict(),os.path.join(model_dir,'preRN18_last.pt'))
        
        natural_acc.append(natural_err_total.cpu().item())
        robust_acc.append(robust_err_total.cpu().item())
        print('================================================================')
        
        file_name = os.path.join(log_dir, 'train_stats.npy')
        np.save(file_name, np.stack((np.array(natural_acc), np.array(robust_acc))))        

        # save checkpoint
        #if epoch % args.save_freq == 0:
        #    torch.save(model.state_dict(),
        #               os.path.join(model_dir, 'model-res-epoch{}.pt'.format(epoch)))
        #    torch.save(optimizer.state_dict(),
        #               os.path.join(model_dir, 'opt-res-checkpoint_epoch{}.tar'.format(epoch)))


if __name__ == '__main__':
    main()